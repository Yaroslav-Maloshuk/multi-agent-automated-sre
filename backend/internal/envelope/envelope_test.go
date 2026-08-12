package envelope

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestNewSetsMetadata(t *testing.T) {
	e := New(IncidentCreated, ChannelIncident("i1"))
	if e.Version != 1 {
		t.Fatalf("version = %d, want 1", e.Version)
	}
	if !strings.HasPrefix(e.ID, "evt_") || len(e.ID) != len("evt_")+32 {
		t.Fatalf("bad event id %q", e.ID)
	}
	if e.Timestamp == "" {
		t.Fatal("timestamp empty")
	}
	if e.Payload == nil {
		t.Fatal("payload nil")
	}
}

func TestWithSetters(t *testing.T) {
	e := New(GraphStarted, "system").
		WithIncident("inc_1").
		WithRun("run_1").
		WithCorrelation("corr_1").
		WithPayload(map[string]any{"x": float64(1)})
	if e.IncidentID == nil || *e.IncidentID != "inc_1" {
		t.Fatalf("incident = %v", e.IncidentID)
	}
	if e.RunID == nil || *e.RunID != "run_1" {
		t.Fatalf("run = %v", e.RunID)
	}
	if e.CorrelationID == nil || *e.CorrelationID != "corr_1" {
		t.Fatalf("corr = %v", e.CorrelationID)
	}
	if e.Payload["x"] != float64(1) {
		t.Fatalf("payload = %v", e.Payload)
	}
}

func TestMarshalParseRoundTrip(t *testing.T) {
	e := New(AgentLog, ChannelIncident("inc_7")).WithPayload(map[string]any{"text": "hello"})
	raw, err := e.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	back, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if back.Type != AgentLog || back.Channel != "incident:inc_7" {
		t.Fatalf("round trip mismatch: %+v", back)
	}
	if back.Payload["text"] != "hello" {
		t.Fatalf("payload mismatch: %v", back.Payload)
	}
}

func TestChannelHelpers(t *testing.T) {
	if got := ChannelIncident("a:b"); got != "incident:a:b" {
		t.Fatalf("ChannelIncident = %q", got)
	}
	if got := ChannelRun("r"); got != "run:r" {
		t.Fatalf("ChannelRun = %q", got)
	}
}

func TestPayloadJSONDecodesAsMap(t *testing.T) {
	raw := []byte(`{"version":1,"id":"evt_x","type":"agent.log","channel":"incident:i","timestamp":"t","payload":{"text":"hi"}}`)
	e, err := Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	if e.Payload["text"] != "hi" {
		t.Fatalf("payload = %v", e.Payload)
	}
	_ = json.Valid(raw)
}
