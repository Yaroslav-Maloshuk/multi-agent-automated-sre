package roi

import (
	"testing"
	"time"

	"auto-sre/backend/internal/config"
)

func cfg() config.Config {
	return config.Config{
		TokenPriceIn1K:         0.005,
		TokenPriceOut1K:        0.015,
		HumanMTTRSeconds:       35 * 60, // 2100s
		HumanLoadedCostPerHour: 150.0,
	}
}

func terminalState(status string) map[string]any {
	return map[string]any{
		"status":       status,
		"incident_id":  "inc_1",
		"created_at":   time.Now().UTC().Add(-10 * time.Minute).Format(time.RFC3339),
		"completed_at": time.Now().UTC().Format(time.RFC3339),
		"token_usage": map[string]any{
			"input_tokens":  20000,
			"output_tokens": 5000,
		},
	}
}

func TestComputeCompletedSavesMoney(t *testing.T) {
	row := Compute(terminalState("COMPLETED"), cfg())
	if row == nil {
		t.Fatal("expected a row for terminal state")
	}
	if row.SavedSeconds <= 0 {
		t.Fatalf("saved seconds = %v, want > 0", row.SavedSeconds)
	}
	if row.SavedAmount <= 0 {
		t.Fatalf("saved amount = %v, want > 0", row.SavedAmount)
	}
	// 25k tokens * 150s/2100s... verify token cost math:
	// in = 20000/1000*0.005 = 0.10 ; out = 5000/1000*0.015 = 0.075 → 0.175
	if row.TokenCost != 0.175 {
		t.Fatalf("token cost = %v, want 0.175", row.TokenCost)
	}
}

func TestComputeFailedYieldsNoSavings(t *testing.T) {
	row := Compute(terminalState("FAILED"), cfg())
	if row == nil {
		t.Fatal("expected a row for failed state")
	}
	if row.SavedAmount != 0 {
		t.Fatalf("failed run should save nothing, got %v", row.SavedAmount)
	}
}

func TestComputeNonTerminalIsNil(t *testing.T) {
	if row := Compute(terminalState("RUNNING"), cfg()); row != nil {
		t.Fatalf("non-terminal must not produce a ledger row, got %+v", row)
	}
}

func TestComputeMissingIncidentIsNil(t *testing.T) {
	st := terminalState("COMPLETED")
	delete(st, "incident_id")
	if row := Compute(st, cfg()); row != nil {
		t.Fatalf("missing incident must yield nil, got %+v", row)
	}
}

func TestComputeDurationAndMTTR(t *testing.T) {
	st := terminalState("COMPLETED")
	row := Compute(st, cfg())
	if row.DurationSeconds < 590 || row.DurationSeconds > 620 {
		t.Fatalf("duration = %v, want ~600", row.DurationSeconds)
	}
	if row.HumanMTTRSeconds != 2100 {
		t.Fatalf("mttr = %d, want 2100", row.HumanMTTRSeconds)
	}
}

func TestTerminalSet(t *testing.T) {
	for _, status := range []string{"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"} {
		if !Terminal[status] {
			t.Fatalf("%s should be terminal", status)
		}
	}
}
