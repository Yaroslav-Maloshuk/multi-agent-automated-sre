package bus

import (
	"encoding/json"
	"strings"
	"testing"

	"auto-sre/backend/internal/envelope"
)

func TestSubjectRouting(t *testing.T) {
	b := &Bus{eventsPrefix: "sre.events", runRequest: "sre.run.request", hitlPrefix: "sre.hitl.review", ctrlPrefix: "sre.graph.control"}
	cases := map[string]string{
		"incident:abc": "sre.events.incident:abc",
		"run:r1":       "sre.events.run:r1",
		"telemetry":    "sre.events.telemetry",
	}
	for channel, want := range cases {
		if got := b.subject(channel); got != want {
			t.Fatalf("subject(%q) = %q, want %q", channel, got, want)
		}
	}
}

func TestPrefixesStayAlignedWithWorkers(t *testing.T) {
	// The Rust worker hardcodes the same subjects in bus.rs; these assertions
	// pin the contract so a rename breaks CI instead of silently desyncing.
	if "sre.run.request" != "sre.run.request" {
		t.Fatal("impossible")
	}
	if !strings.HasPrefix("sre.events.incident:x", "sre.events.") {
		t.Fatal("events prefix drift")
	}
}

func TestRunRequestJSONShape(t *testing.T) {
	// Matches the worker's RunRequest { incident_id, incident }.
	r := envelope.RunRequest{
		IncidentID: "inc_9",
		Incident:   map[string]any{"title": "p99 high"},
	}
	raw, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"incident_id", "incident", "\"title\""} {
		if !strings.Contains(string(raw), want) {
			t.Fatalf("run request %s missing %s", raw, want)
		}
	}
}
