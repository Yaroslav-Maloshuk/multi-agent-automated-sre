// Package roi implements the business ROI ledger: it converts agent latency +
// token spend into dollars vs. the human MTTR baseline at a loaded hourly rate.
// A faithful port of legacy app/services/roi.py, backed by Go/SQL.
package roi

import (
	"time"

	"auto-sre/backend/internal/config"
	"auto-sre/backend/internal/store"
)

var Terminal = map[string]bool{
	"COMPLETED":   true,
	"FAILED":      true,
	"CANCELLED":   true,
	"INTERRUPTED": true,
}

// Compute derives a ledger row from a worker terminal state snapshot. Returns
// nil when the state is not terminal (mirrors Python's zero-entry behaviour).
func Compute(state map[string]any, cfg config.Config) *store.RoiRow {
	status, _ := state["status"].(string)
	incidentID, _ := state["incident_id"].(string)
	if incidentID == "" || !Terminal[status] {
		return nil
	}

	created := parseTime(state["created_at"])
	completed := parseTime(state["completed_at"])
	var duration float64
	if completed.After(created) {
		duration = completed.Sub(created).Seconds()
	}

	in, out := tokens(state)
	cost := float64(in)/1000*cfg.TokenPriceIn1K + float64(out)/1000*cfg.TokenPriceOut1K

	var savedSeconds float64
	if status == "COMPLETED" {
		if v := float64(cfg.HumanMTTRSeconds) - duration; v > 0 {
			savedSeconds = v
		}
	}
	savedAmount := savedSeconds / 3600 * cfg.HumanLoadedCostPerHour
	if savedAmount -= cost; savedAmount < 0 {
		savedAmount = 0
	}

	return &store.RoiRow{
		IncidentID:       incidentID,
		Status:           status,
		DurationSeconds:  duration,
		HumanMTTRSeconds: int64(cfg.HumanMTTRSeconds),
		InputTokens:      in,
		OutputTokens:     out,
		TokenCost:        cost,
		SavedSeconds:     savedSeconds,
		SavedAmount:      savedAmount,
	}
}

func tokens(state map[string]any) (in, out int64) {
	usage, _ := state["token_usage"].(map[string]any)
	if usage == nil {
		return 0, 0
	}
	in = int64(toFloat(usage["input_tokens"]))
	out = int64(toFloat(usage["output_tokens"]))
	return in, out
}

func parseTime(v any) time.Time {
	s, _ := v.(string)
	if s == "" {
		return time.Now().UTC()
	}
	if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
		return t
	}
	return time.Now().UTC()
}

func toFloat(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return 0
}
