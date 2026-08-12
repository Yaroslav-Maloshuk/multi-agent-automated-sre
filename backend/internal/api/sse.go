package api

import (
	"fmt"
	"net/http"
	"time"

	"auto-sre/backend/internal/envelope"
)

// handleSSE streams the worker event fabric as Server-Sent Events so the HTMX
// dashboard (and any long-polling client) sees the same envelopes as the
// WebSocket command center — zero polling.
func (a *App) handleSSE(w http.ResponseWriter, r *http.Request) {
	channels := parseChannels(r.URL.Query().Get("channels"))
	if len(channels) == 0 {
		channels = []string{"*"}
	}

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("X-Accel-Buffering", "no")

	sub, cleanup := a.Hub.SubscribeSSE(channels)
	defer cleanup()

	hello := envelope.New(envelope.EventType("system.ready"), envelope.ChannelSystem)
	if raw, err := hello.Marshal(); err == nil {
		fmt.Fprintf(w, "event: message\ndata: %s\n\n", raw)
		flusher.Flush()
	}

	ctx := r.Context()
	keepalive := time.NewTicker(15 * time.Second)
	defer keepalive.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case raw, ok := <-sub:
			if !ok {
				return
			}
			fmt.Fprintf(w, "event: message\ndata: %s\n\n", raw)
			flusher.Flush()
		case <-keepalive.C:
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
		}
	}
}
