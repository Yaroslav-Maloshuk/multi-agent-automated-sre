// Package store is the Go/SQL persistence layer (pgx + embedded migration).
// Incidents, live agent state, snapshots and the ROI ledger all live in
// PostgreSQL — replacing the Prisma ORM with plain, type-safe SQL.
package store

import (
	"context"
	_ "embed"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed 001_init.sql
var schema string

type Store struct {
	pool *pgxpool.Pool
}

type RoiRow struct {
	IncidentID       string
	Status           string
	DurationSeconds  float64
	HumanMTTRSeconds int64
	InputTokens      int64
	OutputTokens     int64
	TokenCost        float64
	SavedSeconds     float64
	SavedAmount      float64
}

type SnapshotRow struct {
	Ref        string
	IncidentID string
	RunID      string
	StepIndex  int
	Node       string
	Summary    string
	Status     string
	Blob       json.RawMessage
}

func Open(ctx context.Context, url string) (*Store, error) {
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if _, err := pool.Exec(ctx, schema); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() { s.pool.Close() }

func (s *Store) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
}

func (s *Store) UpsertIncident(ctx context.Context, id string, payload map[string]any) error {
	raw, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx,
		`INSERT INTO incidents (incident_id, payload) VALUES ($1, $2)
		 ON CONFLICT (incident_id) DO UPDATE SET payload = EXCLUDED.payload`,
		id, raw)
	return err
}

func (s *Store) GetIncident(ctx context.Context, id string) (map[string]any, error) {
	var raw []byte
	err := s.pool.QueryRow(ctx, `SELECT payload FROM incidents WHERE incident_id = $1`, id).Scan(&raw)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func (s *Store) ListIncidents(ctx context.Context, limit int) ([]map[string]any, error) {
	if limit <= 0 {
		limit = 200
	}
	rows, err := s.pool.Query(ctx,
		`SELECT payload FROM incidents ORDER BY created_at DESC LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []map[string]any
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		var m map[string]any
		if err := json.Unmarshal(raw, &m); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// UpsertState stores the latest live agent state (absorbe'd from
// `memory.snapshot` envelopes published by the Rust workers).
func (s *Store) UpsertState(ctx context.Context, incidentID string, state map[string]any) error {
	raw, err := json.Marshal(state)
	if err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx,
		`INSERT INTO incident_states (incident_id, state, updated_at) VALUES ($1, $2, now())
		 ON CONFLICT (incident_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now()`,
		incidentID, raw)
	return err
}

func (s *Store) GetState(ctx context.Context, incidentID string) (map[string]any, error) {
	var raw []byte
	err := s.pool.QueryRow(ctx, `SELECT state FROM incident_states WHERE incident_id = $1`, incidentID).Scan(&raw)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	return m, nil
}

func (s *Store) InsertSnapshot(ctx context.Context, snap SnapshotRow) error {
	_, err := s.pool.Exec(ctx,
		`INSERT INTO snapshots (ref, incident_id, run_id, step_index, node, summary, status, blob, created_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
		 ON CONFLICT (ref) DO UPDATE SET blob = EXCLUDED.blob`,
		snap.Ref, snap.IncidentID, snap.RunID, snap.StepIndex, snap.Node, snap.Summary, snap.Status, snap.Blob)
	return err
}

func (s *Store) ListSnapshots(ctx context.Context, incidentID string) ([]SnapshotRow, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT ref, incident_id, run_id, step_index, node, summary, status, blob
		 FROM snapshots WHERE incident_id = $1 ORDER BY step_index ASC`, incidentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SnapshotRow
	for rows.Next() {
		var r SnapshotRow
		if err := rows.Scan(&r.Ref, &r.IncidentID, &r.RunID, &r.StepIndex, &r.Node, &r.Summary, &r.Status, &r.Blob); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

func (s *Store) GetSnapshot(ctx context.Context, ref string) (*SnapshotRow, error) {
	var r SnapshotRow
	err := s.pool.QueryRow(ctx,
		`SELECT ref, incident_id, run_id, step_index, node, summary, status, blob
		 FROM snapshots WHERE ref = $1`, ref).
		Scan(&r.Ref, &r.IncidentID, &r.RunID, &r.StepIndex, &r.Node, &r.Summary, &r.Status, &r.Blob)
	if err != nil {
		return nil, err
	}
	return &r, nil
}

func (s *Store) UpsertRoi(ctx context.Context, r RoiRow) error {
	_, err := s.pool.Exec(ctx,
		`INSERT INTO roi_ledger (
			incident_id, status, duration_seconds, human_mttr_seconds,
			input_tokens, output_tokens, token_cost, saved_seconds, saved_amount, recorded_at
		 ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
		 ON CONFLICT (incident_id) DO UPDATE SET
			status = EXCLUDED.status,
			duration_seconds = EXCLUDED.duration_seconds,
			human_mttr_seconds = EXCLUDED.human_mttr_seconds,
			input_tokens = EXCLUDED.input_tokens,
			output_tokens = EXCLUDED.output_tokens,
			token_cost = EXCLUDED.token_cost,
			saved_seconds = EXCLUDED.saved_seconds,
			saved_amount = EXCLUDED.saved_amount`,
		r.IncidentID, r.Status, r.DurationSeconds, r.HumanMTTRSeconds,
		r.InputTokens, r.OutputTokens, r.TokenCost, r.SavedSeconds, r.SavedAmount)
	return err
}

func (s *Store) GetRoi(ctx context.Context, incidentID string) (map[string]any, error) {
	var raw struct {
		IncidentID       string
		Status           string
		DurationSeconds  float64
		HumanMTTRSeconds int64
		InputTokens      int64
		OutputTokens     int64
		TokenCost        float64
		SavedSeconds     float64
		SavedAmount      float64
	}
	err := s.pool.QueryRow(ctx, `SELECT incident_id, status, duration_seconds, human_mttr_seconds,
		input_tokens, output_tokens, token_cost, saved_seconds, saved_amount
		FROM roi_ledger WHERE incident_id = $1`, incidentID).
		Scan(&raw.IncidentID, &raw.Status, &raw.DurationSeconds, &raw.HumanMTTRSeconds,
			&raw.InputTokens, &raw.OutputTokens, &raw.TokenCost, &raw.SavedSeconds, &raw.SavedAmount)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	return map[string]any{
		"incident_id":        raw.IncidentID,
		"status":             raw.Status,
		"duration_seconds":   round2(raw.DurationSeconds),
		"human_mttr_seconds": raw.HumanMTTRSeconds,
		"input_tokens":       raw.InputTokens,
		"output_tokens":      raw.OutputTokens,
		"token_cost":         round4(raw.TokenCost),
		"saved_seconds":      round2(raw.SavedSeconds),
		"saved_amount":       round2(raw.SavedAmount),
	}, nil
}

func (s *Store) RoiSummary(ctx context.Context) (map[string]any, error) {
	var totalIncidents, resolved int
	var totalTokens int64
	var totalTokenCost, totalSavedAmount, totalSavedSeconds, avgDuration float64
	err := s.pool.QueryRow(ctx,
		`SELECT count(*),
		        count(*) FILTER (WHERE status = 'COMPLETED'),
		        COALESCE(sum(input_tokens + output_tokens), 0),
		        COALESCE(sum(token_cost), 0),
		        COALESCE(sum(saved_amount), 0),
		        COALESCE(sum(saved_seconds), 0),
		        COALESCE(avg(duration_seconds), 0)
		 FROM roi_ledger`).
		Scan(&totalIncidents, &resolved, &totalTokens, &totalTokenCost, &totalSavedAmount, &totalSavedSeconds, &avgDuration)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"total_incidents":      totalIncidents,
		"resolved":             resolved,
		"total_tokens":         totalTokens,
		"total_token_cost":     round4(totalTokenCost),
		"total_saved_amount":   round2(totalSavedAmount),
		"total_saved_seconds":  round2(totalSavedSeconds),
		"avg_duration_seconds": round2(avgDuration),
		"human_mttr_seconds":   0,
		"loaded_cost_per_hour": 0,
	}, nil
}

func (s *Store) RoiHistory(ctx context.Context, limit int) ([]map[string]any, error) {
	if limit <= 0 {
		limit = 200
	}
	rows, err := s.pool.Query(ctx,
		`SELECT incident_id, status, duration_seconds, human_mttr_seconds, input_tokens, output_tokens,
		        token_cost, saved_seconds, saved_amount
		 FROM roi_ledger ORDER BY recorded_at ASC LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []map[string]any
	for rows.Next() {
		var r RoiRow
		if err := rows.Scan(&r.IncidentID, &r.Status, &r.DurationSeconds, &r.HumanMTTRSeconds,
			&r.InputTokens, &r.OutputTokens, &r.TokenCost, &r.SavedSeconds, &r.SavedAmount); err != nil {
			return nil, err
		}
		out = append(out, map[string]any{
			"incident_id":        r.IncidentID,
			"status":             r.Status,
			"duration_seconds":   round2(r.DurationSeconds),
			"human_mttr_seconds": r.HumanMTTRSeconds,
			"input_tokens":       r.InputTokens,
			"output_tokens":      r.OutputTokens,
			"token_cost":         round4(r.TokenCost),
			"saved_seconds":      round2(r.SavedSeconds),
			"saved_amount":       round2(r.SavedAmount),
		})
	}
	return out, rows.Err()
}

func round2(v float64) float64 { return float64(int(v*100+0.5)) / 100 }
func round4(v float64) float64 { return float64(int(v*10000+0.5)) / 10000 }

var _ = time.Now
