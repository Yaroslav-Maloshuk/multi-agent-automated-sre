package store

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func testStore(t *testing.T) *Store {
	t.Helper()
	url := os.Getenv("DATABASE_URL_TEST")
	if url == "" {
		t.Skip("set DATABASE_URL_TEST for store integration tests")
	}
	s, err := Open(context.Background(), url)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(s.Close)
	return s
}

func TestIncidentUpsertRoundTrip(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	id := "inc_store_test"

	if err := s.UpsertIncident(ctx, id, map[string]any{"title": "first", "severity": "P2"}); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	got, err := s.GetIncident(ctx, id)
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got["title"] != "first" {
		t.Fatalf("title = %v", got["title"])
	}

	// upsert overwrites
	if err := s.UpsertIncident(ctx, id, map[string]any{"title": "second"}); err != nil {
		t.Fatalf("upsert2: %v", err)
	}
	got, _ = s.GetIncident(ctx, id)
	if got["title"] != "second" {
		t.Fatalf("title after upsert = %v", got["title"])
	}
}

func TestRoiLedgerInsertAndQuery(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	incidentID := "inc_roi_test"

	if err := s.UpsertRoi(ctx, RoiRow{
		IncidentID:       incidentID,
		Status:           "COMPLETED",
		DurationSeconds:  600,
		HumanMTTRSeconds: 2100,
		InputTokens:      1000,
		OutputTokens:     200,
		TokenCost:        0.008,
		SavedSeconds:     1500,
		SavedAmount:      62.5,
	}); err != nil {
		t.Fatalf("insert roi: %v", err)
	}

	row, err := s.GetRoi(ctx, incidentID)
	if err != nil {
		t.Fatalf("get roi: %v", err)
	}
	if row == nil {
		t.Fatal("roi row missing")
	}
	if row["saved_amount"] != 62.5 {
		t.Fatalf("saved = %v, want 62.5", row["saved_amount"])
	}
}

func TestSnapshotInsertAndGet(t *testing.T) {
	s := testStore(t)
	ctx := context.Background()
	row := SnapshotRow{
		Ref:        "snap_test_1",
		IncidentID: "inc_snap",
		RunID:      "run_snap",
		StepIndex:  2,
		Node:       "triage",
		Summary:    "diagnosed",
		Status:     "RUNNING",
		Blob:       json.RawMessage(`{"status":"RUNNING"}`),
	}
	if err := s.InsertSnapshot(ctx, row); err != nil {
		t.Fatalf("insert snapshot: %v", err)
	}
	back, err := s.GetSnapshot(ctx, row.Ref)
	if err != nil {
		t.Fatalf("get snapshot: %v", err)
	}
	if back.Node != "triage" {
		t.Fatalf("node = %q, want triage", back.Node)
	}
	if back.StepIndex != 2 {
		t.Fatalf("step = %d, want 2", back.StepIndex)
	}
	if back.Summary != "diagnosed" {
		t.Fatalf("summary = %q, want diagnosed", back.Summary)
	}
}
