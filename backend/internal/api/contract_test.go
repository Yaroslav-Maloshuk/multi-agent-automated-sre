package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"auto-sre/backend/internal/bus"
	"auto-sre/backend/internal/config"
	"auto-sre/backend/internal/store"
)

// ContractTestGate skips the suite when the backing services are absent so
// the unit pipeline stays green; CI runs it against real NATS + Postgres.
func ContractTestGate(t *testing.T) (string, string) {
	t.Helper()
	db := os.Getenv("DATABASE_URL_TEST")
	natsURL := os.Getenv("NATS_URL_TEST")
	if db == "" || natsURL == "" {
		t.Skip("set DATABASE_URL_TEST and NATS_URL_TEST for contract tests")
	}
	return db, natsURL
}

func contractApp(t *testing.T) *App {
	t.Helper()
	db, natsURL := ContractTestGate(t)

	st, err := store.Open(context.Background(), db)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(st.Close)

	b, err := bus.Connect(natsURL)
	if err != nil {
		t.Fatalf("connect nats: %v", err)
	}
	t.Cleanup(b.Close)

	cfg := config.Config{
		GraphHITLAutoApprove: true, // keep the contract test fully autonomous
	}
	a := New(cfg, st, b, nil)
	return a
}

func doJSON(t *testing.T, handler http.Handler, method, path string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var raw []byte
	if body != nil {
		raw, _ = json.Marshal(body)
	}
	req := httptest.NewRequest(method, path, bytes.NewReader(raw))
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

func TestContractLifecycle(t *testing.T) {
	a := contractApp(t)
	handler := a.Routes()

	// health
	if rec := doJSON(t, handler, "GET", "/api/v1/health", nil); rec.Code != http.StatusOK {
		t.Fatalf("health = %d", rec.Code)
	}

	// create incident
	rec := doJSON(t, handler, "POST", "/api/v1/incidents", map[string]any{
		"title":    "contract-test high p99",
		"severity": "P1",
		"tags":     []string{"contract"},
	})
	if rec.Code != http.StatusCreated && rec.Code != http.StatusOK {
		t.Fatalf("create = %d: %s", rec.Code, rec.Body.String())
	}
	var created struct {
		IncidentID string `json:"incident_id"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &created); err != nil {
		t.Fatalf("decode create: %v (%s)", err, rec.Body.String())
	}
	if created.IncidentID == "" {
		t.Fatal("no incident_id returned")
	}
	id := created.IncidentID

	// run it (worker may be absent; gateway must still accept and dispatch)
	if rec := doJSON(t, handler, "POST", "/api/v1/incidents/"+id+"/run", nil); rec.Code != http.StatusAccepted && rec.Code != http.StatusOK {
		t.Fatalf("run = %d: %s", rec.Code, rec.Body.String())
	}

	// fetch it back
	if rec := doJSON(t, handler, "GET", "/api/v1/incidents/"+id, nil); rec.Code != http.StatusOK {
		t.Fatalf("get = %d", rec.Code)
	}

	// list
	if rec := doJSON(t, handler, "GET", "/api/v1/incidents", nil); rec.Code != http.StatusOK {
		t.Fatalf("list = %d", rec.Code)
	}

	// interrupt + resume are accepted
	if rec := doJSON(t, handler, "POST", "/api/v1/incidents/"+id+"/interrupt", nil); rec.Code != http.StatusAccepted && rec.Code != http.StatusOK {
		t.Fatalf("interrupt = %d: %s", rec.Code, rec.Body.String())
	}
	if rec := doJSON(t, handler, "POST", "/api/v1/incidents/"+id+"/resume", nil); rec.Code != http.StatusAccepted && rec.Code != http.StatusOK {
		t.Fatalf("resume = %d: %s", rec.Code, rec.Body.String())
	}
}

func TestContractRejectsMalformedIncident(t *testing.T) {
	a := contractApp(t)
	handler := a.Routes()
	rec := doJSON(t, handler, "POST", "/api/v1/incidents", map[string]any{"nope": true})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("malformed incident = %d, want 400", rec.Code)
	}
}

func TestContract404(t *testing.T) {
	a := contractApp(t)
	handler := a.Routes()
	rec := doJSON(t, handler, "GET", "/api/v1/incidents/does-not-exist", nil)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("missing incident = %d, want 404", rec.Code)
	}
}
