package api

import (
	"context"
	"log"
	"net/http"
	"strings"
	"time"

	"auto-sre/backend/internal/envelope"
	"auto-sre/backend/internal/ws"
)

func (a *App) Routes() http.Handler {
	onControl := func(msg envelope.WSClientMessage) {
		incidentID := ""
		if msg.IncidentID != nil {
			incidentID = *msg.IncidentID
		}
		switch msg.Action {
		case "hitl.review":
			if incidentID == "" {
				return
			}
			action := envelope.ReviewAction("")
			if msg.ActionValue != nil {
				action = envelope.ReviewAction(*msg.ActionValue)
			}
			if v, ok := msg.Payload["action"].(string); ok {
				action = envelope.ReviewAction(v)
			}
			reason := msg.Reason
			modified := map[string]string{}
			if v, ok := msg.Payload["modified_files"].(map[string]any); ok {
				for k, val := range v {
					if s, ok := val.(string); ok {
						modified[k] = s
					}
				}
			}
			patchRef := ""
			if msg.PatchRef != nil {
				patchRef = *msg.PatchRef
			}
			_ = a.Bus.PublishReview(incidentID, envelope.HITLReview{
				IncidentID:    incidentID,
				PatchRef:      patchRef,
				Action:        action,
				Reason:        reason,
				ModifiedFiles: modified,
			})
		case "graph.resume":
			if incidentID != "" {
				incident, err := a.Store.GetIncident(context.Background(), incidentID)
				if err == nil {
					_ = a.Bus.PublishRunRequest(envelope.RunRequest{IncidentID: incidentID, Incident: incident})
				}
			}
		case "graph.interrupt":
			if incidentID != "" {
				_ = a.Bus.PublishControl(incidentID, envelope.ControlMessage{Action: "interrupt"})
			}
		}
	}

	a.Hub.SetControlHandler(onControl)

	mux := http.NewServeMux()

	mux.HandleFunc("GET /", a.handleDashboard)
	mux.HandleFunc("GET /api/v1/dashboard/incidents", a.handleDashboardIncidents)

	mux.HandleFunc("GET /api/v1/health", a.handleHealth)

	mux.HandleFunc("POST /api/v1/incidents", a.handleCreateIncident)
	mux.HandleFunc("GET /api/v1/incidents", a.handleListIncidents)
	mux.HandleFunc("GET /api/v1/incidents/{incident_id}", a.handleGetIncident)
	mux.HandleFunc("POST /api/v1/incidents/{incident_id}/run", a.handleRunIncident)
	mux.HandleFunc("POST /api/v1/incidents/{incident_id}/review", a.handleReviewIncident)
	mux.HandleFunc("POST /api/v1/incidents/{incident_id}/interrupt", a.handleInterruptIncident)
	mux.HandleFunc("POST /api/v1/incidents/{incident_id}/resume", a.handleResumeIncident)
	mux.HandleFunc("GET /api/v1/incidents/{incident_id}/snapshots", a.handleListSnapshots)
	mux.HandleFunc("GET /api/v1/incidents/{incident_id}/snapshots/{ref}", a.handleGetSnapshot)
	mux.HandleFunc("POST /api/v1/incidents/{incident_id}/fork", a.handleForkIncident)

	mux.HandleFunc("GET /api/v1/agents", a.handleListAgents)
	mux.HandleFunc("GET /api/v1/sandboxes", a.handleSandboxStats)
	mux.HandleFunc("POST /api/v1/sandboxes/warm", a.handleWarmSandboxes)
	mux.HandleFunc("GET /api/v1/metrics/roi", a.handleROI)

	mux.HandleFunc("POST /api/v1/collectors/datadog", a.handleCollectorWebhook("datadog"))
	mux.HandleFunc("POST /api/v1/collectors/prometheus", a.handleCollectorWebhook("prometheus"))
	mux.HandleFunc("POST /api/v1/collectors/kubernetes", a.handleCollectorWebhook("kubernetes"))
	mux.HandleFunc("GET /api/v1/collectors", a.handleCollectorStatus)

	mux.HandleFunc("GET /ws/stream", a.handleWebSocket(a.Hub))
	mux.HandleFunc("GET /api/v1/events/stream", a.handleSSE)

	return a.cors(a.logging(mux))
}

func (a *App) handleWebSocket(hub *ws.Hub) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		channels := parseChannels(r.URL.Query().Get("channels"))
		if len(channels) == 0 {
			channels = []string{"*"}
		}
		hub.ServeWS(w, r, channels)
	}
}

func parseChannels(raw string) []string {
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func (a *App) cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (a *App) logging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("%s %s %s", r.Method, r.URL.Path, time.Since(start))
	})
}
