// Package api wires the Go gateway: REST surface, WebSocket command center,
// NATS event absorption, snapshot persistence and the ROI ledger.
package api

import (
	"context"
	"log"
	"sync"
	"time"

	"auto-sre/backend/internal/bus"
	"auto-sre/backend/internal/config"
	"auto-sre/backend/internal/envelope"
	"auto-sre/backend/internal/roi"
	"auto-sre/backend/internal/store"
	"auto-sre/backend/internal/ws"
)

type App struct {
	Cfg   config.Config
	Store *store.Store
	Bus   *bus.Bus
	Hub   *ws.Hub

	mu      sync.Mutex
	running map[string]bool // incident_id -> in-flight run (or pending approval)
}

func New(cfg config.Config, st *store.Store, b *bus.Bus, hub *ws.Hub) *App {
	if hub == nil {
		hub = ws.NewHub(nil)
	}
	go hub.Run()
	return &App{
		Cfg:     cfg,
		Store:   st,
		Bus:     b,
		Hub:     hub,
		running: map[string]bool{},
	}
}

// Start absorbs worker envelopes from NATS: fans out to the WebSocket hub,
// persists live state + time-travel snapshots, and records the ROI ledger on
// terminal runs.
func (a *App) Start(ctx context.Context) error {
	return a.Bus.SubscribeEvents(func(env envelope.Envelope) {
		a.absorb(env)
	})
}

func (a *App) absorb(env envelope.Envelope) {
	a.Hub.Broadcast(env)

	switch env.Type {
	case envelope.MemorySnapshot:
		ref, _ := env.Payload["ref"].(string)
		state, _ := env.Payload["state"].(map[string]any)
		if state == nil {
			return
		}
		if err := a.Store.UpsertState(context.Background(), envIncidentID(env, state), state); err != nil {
			log.Printf("store: state upsert failed: %v", err)
		}
		if ref != "" {
			a.persistSnapshot(env, state, ref)
		}
		if status, _ := state["status"].(string); roi.Terminal[status] {
			a.finishRun(state)
		}
	}
}

func (a *App) persistSnapshot(env envelope.Envelope, state map[string]any, ref string) {
	node, _ := env.Payload["node"].(string)
	summary, _ := env.Payload["summary"].(string)
	status, _ := state["status"].(string)
	incidentID := envIncidentID(env, state)
	runID, _ := state["run_id"].(string)
	stepIndex := int(int64Of(state["step_index"]))

	blob, err := marshalAny(state)
	if err != nil {
		return
	}
	_ = a.Store.InsertSnapshot(context.Background(), store.SnapshotRow{
		Ref:        ref,
		IncidentID: incidentID,
		RunID:      runID,
		StepIndex:  stepIndex,
		Node:       node,
		Summary:    summary,
		Status:     status,
		Blob:       blob,
	})
}

func (a *App) finishRun(state map[string]any) {
	incidentID, _ := state["incident_id"].(string)
	a.mu.Lock()
	delete(a.running, incidentID)
	a.mu.Unlock()

	row := roi.Compute(state, a.Cfg)
	if row == nil {
		return
	}
	if err := a.Store.UpsertRoi(context.Background(), *row); err != nil {
		log.Printf("store: roi upsert failed: %v", err)
		return
	}
	env := envelope.New(envelope.ROIUpdated, envelope.ChannelTelemetry).
		WithIncident(incidentID).
		WithPayload(map[string]any{"incident_id": incidentID})
	_ = a.Bus.PublishEnvelope(env)
}

func (a *App) IsRunning(incidentID string) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.running[incidentID]
}

func (a *App) MarkRunning(incidentID string) {
	a.mu.Lock()
	a.running[incidentID] = true
	a.mu.Unlock()
}

func (a *App) LiveStatus(ctx context.Context, incidentID string) string {
	st, err := a.Store.GetState(ctx, incidentID)
	if err != nil || st == nil {
		return "QUEUED"
	}
	status, _ := st["status"].(string)
	if status == "" {
		return "QUEUED"
	}
	return status
}

func envIncidentID(env envelope.Envelope, state map[string]any) string {
	if env.IncidentID != nil {
		return *env.IncidentID
	}
	id, _ := state["incident_id"].(string)
	return id
}

func int64Of(v any) int64 {
	switch n := v.(type) {
	case float64:
		return int64(n)
	case int:
		return int64(n)
	case int64:
		return n
	}
	return 0
}

var _ = time.Now
