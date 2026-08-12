package api

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"auto-sre/backend/internal/config"
	"auto-sre/backend/internal/envelope"
	"auto-sre/backend/internal/ws"
)

// mutexWriter gives the SSE handler a Flusher we can read concurrently with a
// -race clean build (the handler writes from its own goroutine).
type mutexWriter struct {
	mu  sync.Mutex
	buf bytes.Buffer
	hdr http.Header
}

func (m *mutexWriter) Header() http.Header {
	if m.hdr == nil {
		m.hdr = http.Header{}
	}
	return m.hdr
}
func (m *mutexWriter) Write(p []byte) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.buf.Write(p)
}
func (m *mutexWriter) WriteHeader(int) {}
func (m *mutexWriter) Flush()          {}
func (m *mutexWriter) String() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.buf.String()
}

func testApp() *App {
	hub := ws.NewHub(nil)
	go hub.Run()
	a := New(config.Config{}, nil, nil, hub)
	return a
}

func TestDashboardRenders(t *testing.T) {
	a := testApp()
	handler := a.Routes()

	rec := doJSON(t, handler, http.MethodGet, "/", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	body := rec.Body.String()
	for _, want := range []string{"htmx", "HTMX dashboard", "Incident", "sse-connect"} {
		if !bytes.Contains([]byte(body), []byte(want)) {
			t.Fatalf("dashboard missing %q", want)
		}
	}
}

func TestDashboardIncidentsFragment(t *testing.T) {
	a := testApp()
	rec := doJSON(t, a.Routes(), http.MethodGet, "/api/v1/dashboard/incidents", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if !bytes.Contains(rec.Body.Bytes(), []byte("incidents-body")) {
		t.Fatal("fragment missing incidents tbody id")
	}
}

func TestSSEStreamsEnvelopes(t *testing.T) {
	a := testApp()
	handler := a.Routes()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req := httptest.NewRequest(http.MethodGet, "/api/v1/events/stream", nil).WithContext(ctx)
	w := &mutexWriter{}

	done := make(chan struct{})
	go func() {
		handler.ServeHTTP(w, req)
		close(done)
	}()

	if !waitFor(t, func() bool { return bytes.Contains([]byte(w.String()), []byte("system.ready")) }) {
		t.Fatal("no ready event emitted")
	}

	a.Hub.Broadcast(envelope.New(envelope.AgentLog, "incident:abc").WithPayload(map[string]any{"text": "hi"}))

	if !waitFor(t, func() bool { return bytes.Contains([]byte(w.String()), []byte("agent.log")) }) {
		t.Fatal("broadcasted envelope never reached the SSE stream")
	}

	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("SSE handler did not shut down on disconnect")
	}
}

func waitFor(t *testing.T, cond func() bool) bool {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return cond()
}
