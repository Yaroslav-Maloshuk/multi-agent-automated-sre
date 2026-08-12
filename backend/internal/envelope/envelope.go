// Package envelope defines the single wire contract shared by the Go gateway,
// the Rust/Tokio workers and the browser over NATS and WebSockets. It mirrors
// legacy/python/app/bus/schemas.py 1:1 so the command center stays untouched.
package envelope

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"time"
)

type EventType string

const (
	IncidentCreated  EventType = "incident.created"
	IncidentUpdated  EventType = "incident.updated"
	GraphStarted     EventType = "graph.started"
	GraphStepStarted EventType = "graph.step.started"
	GraphStepDone    EventType = "graph.step.completed"
	GraphStatus      EventType = "graph.status.changed"
	GraphCompleted   EventType = "graph.completed"
	GraphFailed      EventType = "graph.failed"
	GraphInterrupted EventType = "graph.interrupted"
	GraphResumed     EventType = "graph.resumed"
	AgentStarted     EventType = "agent.started"
	AgentStep        EventType = "agent.step"
	AgentLog         EventType = "agent.log"
	AgentTokenUsage  EventType = "agent.token_usage"
	PatchGenerated   EventType = "patch.generated"
	PatchTyping      EventType = "patch.typing"
	PatchDiff        EventType = "patch.diff"
	PatchApplied     EventType = "patch.applied"
	PendingApproval  EventType = "hitl.pending_approval"
	ReviewDecided    EventType = "hitl.review_decided"
	SandboxAcquired  EventType = "sandbox.acquired"
	SandboxReleased  EventType = "sandbox.released"
	SandboxStdout    EventType = "sandbox.stdout"
	MemorySnapshot   EventType = "memory.snapshot"
	ROIUpdated       EventType = "telemetry.roi"
)

// Envelope is the universal envelope for every message crossing the fabric.
type Envelope struct {
	Version       int            `json:"version"`
	ID            string         `json:"id"`
	Type          EventType      `json:"type"`
	Channel       string         `json:"channel"`
	CorrelationID *string        `json:"correlation_id,omitempty"`
	IncidentID    *string        `json:"incident_id,omitempty"`
	RunID         *string        `json:"run_id,omitempty"`
	Timestamp     string         `json:"timestamp"`
	Payload       map[string]any `json:"payload"`
}

func ChannelIncident(incidentID string) string { return "incident:" + incidentID }
func ChannelRun(runID string) string           { return "run:" + runID }

const (
	ChannelTelemetry = "telemetry"
	ChannelSystem    = "system"
	ChannelSandbox   = "sandbox"
)

func New(typ EventType, channel string) Envelope {
	return Envelope{
		Version:   1,
		ID:        "evt_" + RandomHex(16),
		Type:      typ,
		Channel:   channel,
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Payload:   map[string]any{},
	}
}

func (e Envelope) WithIncident(id string) Envelope {
	e.IncidentID = &id
	return e
}

func (e Envelope) WithRun(id string) Envelope {
	e.RunID = &id
	return e
}

func (e Envelope) WithCorrelation(id string) Envelope {
	e.CorrelationID = &id
	return e
}

func (e Envelope) WithPayload(kv map[string]any) Envelope {
	if e.Payload == nil {
		e.Payload = map[string]any{}
	}
	for k, v := range kv {
		e.Payload[k] = v
	}
	return e
}

func (e Envelope) Marshal() ([]byte, error) {
	return json.Marshal(e)
}

func Parse(raw []byte) (Envelope, error) {
	var e Envelope
	err := json.Unmarshal(raw, &e)
	return e, err
}

// ReviewAction mirrors legacy app.bus.schemas.ReviewAction.
type ReviewAction string

const (
	ReviewApprove ReviewAction = "approve"
	ReviewReject  ReviewAction = "reject"
	ReviewModify  ReviewAction = "modify"
)

// HITLReview is a signed human decision on a pending patch.
type HITLReview struct {
	IncidentID    string            `json:"incident_id"`
	RunID         *string           `json:"run_id,omitempty"`
	PatchRef      string            `json:"patch_ref"`
	Action        ReviewAction      `json:"action"`
	Reason        *string           `json:"reason,omitempty"`
	ModifiedFiles map[string]string `json:"modified_files,omitempty"`
}

// WSClientMessage is a client -> gateway control message.
type WSClientMessage struct {
	Version     int            `json:"version"`
	Action      string         `json:"action"`
	Channel     *string        `json:"channel,omitempty"`
	IncidentID  *string        `json:"incident_id,omitempty"`
	RunID       *string        `json:"run_id,omitempty"`
	Payload     map[string]any `json:"payload"`
	PatchRef    *string        `json:"patch_ref,omitempty"`
	Reason      *string        `json:"reason,omitempty"`
	ActionValue *string        `json:"action_value,omitempty"`
}

// RunRequest is the NATS work-queue payload dispatched to the Rust worker pool.
type RunRequest struct {
	IncidentID string         `json:"incident_id"`
	Incident   map[string]any `json:"incident"`
}

// ControlMessage is a NATS graph control signal (interrupt/resume).
type ControlMessage struct {
	Action string `json:"action"`
	RunID  string `json:"run_id,omitempty"`
}

func RandomHex(n int) string {
	buf := make([]byte, n)
	_, _ = rand.Read(buf)
	return hex.EncodeToString(buf)
}
