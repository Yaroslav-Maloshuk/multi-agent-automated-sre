package main

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/logger"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

type Incident struct {
	IncidentID string
	Status     string
	Severity   string
}

type App struct {
	ctx context.Context
}

func (a *App) Incidents() ([]Incident, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get("http://localhost:8000/api/v1/incidents")
	if err != nil {
		return nil, fmt.Errorf("failed to reach SRE gateway: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("SRE gateway returned status %d", resp.StatusCode)
	}

	var body struct {
		Incidents []Incident `json:"incidents"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, fmt.Errorf("failed to decode incidents response: %w", err)
	}
	return body.Incidents, nil
}

func main() {
	app := &App{}

	err := wails.Run(&options.App{
		Title:  "auto-sre-desktop",
		Width:  1024,
		Height: 768,
		AssetServer: &assetserver.Options{
			Assets: assets,
		},
		BackgroundColour: &options.RGBA{R: 17, G: 17, B: 17, A: 1},
		OnStartup: func(ctx context.Context) {
			app.ctx = ctx
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
