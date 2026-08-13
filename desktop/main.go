package main

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/logger"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
	"github.com/wailsapp/wails/v2/pkg/runtime"
)

//go:embed all:frontend/dist
var assets embed.FS

var (
	gatewayBase = envOr("SRE_GATEWAY_URL", "http://localhost:8000")
	wsBase      = envOr("SRE_WS_URL", "ws://localhost:8000")
)

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

type Incident struct {
	IncidentID string `json:"incident_id"`
	Title      any    `json:"title"`
	Severity   string `json:"severity"`
	Status     string `json:"status"`
}

type App struct {
	ctx  context.Context
	stop chan struct{}
	mu   sync.Mutex
	ws   *websocket.Conn
}

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
	a.stop = make(chan struct{})
}

// Incidents lists all incidents from the gateway.
func (a *App) Incidents() ([]Incident, error) {
	var body struct {
		Incidents []Incident `json:"incidents"`
	}
	if err := a.getJSON("/api/v1/incidents", &body); err != nil {
		return nil, err
	}
	return body.Incidents, nil
}

// IncidentDetail returns the full incident payload + live graph state + ROI.
func (a *App) IncidentDetail(id string) (map[string]any, error) {
	var out map[string]any
	if err := a.getJSON("/api/v1/incidents/"+id, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// CreateIncident registers a new incident; auto_run=false so the operator
// decides when to kick off the agent graph.
func (a *App) CreateIncident(title, summary string) (Incident, error) {
	payload := map[string]any{
		"title":      title,
		"summary":    summary,
		"severity":   "high",
		"source":     "desktop",
		"auto_run":   false,
		"created_at": time.Now().UTC().Format(time.RFC3339),
	}
	var out struct {
		Incident Incident `json:"incident"`
	}
	if err := a.postJSON("/api/v1/incidents", payload, &out); err != nil {
		return Incident{}, err
	}
	return out.Incident, nil
}

// StartRun dispatches the incident through the agent graph on NATS.
func (a *App) StartRun(id string) error {
	return a.postJSON("/api/v1/incidents/"+id+"/run", map[string]any{}, nil)
}

// Interrupt stops an in-flight run.
func (a *App) Interrupt(id string) error {
	return a.postJSON("/api/v1/incidents/"+id+"/interrupt", map[string]any{}, nil)
}

// Review submits a HITL decision: approve | reject | modify.
func (a *App) Review(id, action string) error {
	return a.postJSON("/api/v1/incidents/"+id+"/review", map[string]any{"action": action}, nil)
}

// StartStream opens the gateway WebSocket and forwards every envelope to the
// frontend as a `sre:event` runtime event (zero polling, with backoff).
func (a *App) StartStream() {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.stop = make(chan struct{})

	go func() {
		retries := 0
		url := wsBase + "/ws/stream?channels=*"
		for {
			select {
			case <-a.stop:
				return
			default:
			}
			dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
			conn, _, err := dialer.Dial(url, nil)
			if err != nil {
				retries++
				if a.ctx != nil {
					runtime.EventsEmit(a.ctx, "sre:connection", map[string]any{"connected": false, "retry": retries})
				}
				delay := time.Duration(retries) * 500 * time.Millisecond
				if delay > 30*time.Second {
					delay = 30 * time.Second
				}
				select {
				case <-time.After(delay):
					continue
				case <-a.stop:
					return
				}
			}
			retries = 0
			a.mu.Lock()
			a.ws = conn
			a.mu.Unlock()
			if a.ctx != nil {
				runtime.EventsEmit(a.ctx, "sre:connection", map[string]any{"connected": true})
			}
			for {
				_, data, err := conn.ReadMessage()
				if err != nil {
					conn.Close()
					break
				}
				var ev map[string]any
				if err := json.Unmarshal(data, &ev); err != nil {
					continue
				}
				if a.ctx != nil {
					runtime.EventsEmit(a.ctx, "sre:event", ev)
				}
			}
			select {
			case <-time.After(time.Second):
			case <-a.stop:
				return
			}
		}
	}()
}

// StopStream tears down the live event stream.
func (a *App) StopStream() {
	a.mu.Lock()
	defer a.mu.Unlock()
	close(a.stop)
	if a.ws != nil {
		_ = a.ws.Close()
		a.ws = nil
	}
}

func (a *App) getJSON(path string, out any) error {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(gatewayBase + path)
	if err != nil {
		return fmt.Errorf("SRE gateway unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("SRE gateway returned status %d for GET %s", resp.StatusCode, path)
	}
	if out != nil {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("decode response: %w", err)
		}
	}
	return nil
}

func (a *App) postJSON(path string, payload any, out any) error {
	var reader *strings.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		reader = strings.NewReader(string(raw))
	} else {
		reader = strings.NewReader("{}")
	}
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Post(gatewayBase+path, "application/json", reader)
	if err != nil {
		return fmt.Errorf("SRE gateway unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var e struct {
			Detail string `json:"detail"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&e)
		detail := e.Detail
		if detail == "" {
			detail = http.StatusText(resp.StatusCode)
		}
		return fmt.Errorf("%s failed: %s", path, detail)
	}
	if out != nil {
		if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
			return fmt.Errorf("decode response: %w", err)
		}
	}
	return nil
}

func main() {
	app := &App{}

	err := wails.Run(&options.App{
		Title:  "auto-sre-desktop",
		Width:  1280,
		Height: 860,
		AssetServer: &assetserver.Options{
			Assets: assets,
		},
		BackgroundColour: &options.RGBA{R: 17, G: 17, B: 17, A: 1},
		OnStartup: func(ctx context.Context) {
			app.startup(ctx)
			app.StartStream()
		},
		OnShutdown: func(_ context.Context) {
			app.StopStream()
		},
		Bind: []interface{}{
			app,
		},
		LogLevel: logger.DEBUG,
	})

	if err != nil {
		log.Fatal(err)
	}
}
