// Command gateway is the Go API gateway of the Multi-Agent Automated SRE
// engine. It exposes the REST + WebSocket contract, dispatches runs to the
// Rust/Tokio worker pool over NATS JetStream, and persists state + ROI to
// PostgreSQL via Go/SQL.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"auto-sre/backend/internal/api"
	"auto-sre/backend/internal/bus"
	"auto-sre/backend/internal/config"
	"auto-sre/backend/internal/store"
)

func main() {
	cfg := config.Load()
	log.Printf("auto-sre gateway v%s (%s) — nats=%s postgres=%s", cfg.AppVersion, cfg.AppEnv, cfg.NATSUrl, cfg.PostgresURL)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	st, err := store.Open(ctx, cfg.PostgresURL)
	if err != nil {
		log.Fatalf("postgres: %v", err)
	}
	defer st.Close()

	b, err := bus.Connect(cfg.NATSUrl)
	if err != nil {
		log.Fatalf("nats: %v", err)
	}
	defer b.Close()

	app := api.New(cfg, st, b, nil)
	if err := app.Start(ctx); err != nil {
		log.Fatalf("event absorption: %v", err)
	}

	handler := app.Routes()
	srv := &http.Server{
		Addr:              cfg.Addr,
		Handler:           handler,
		ReadHeaderTimeout: 10 * time.Second,
	}

	go func() {
		log.Printf("gateway listening on %s", cfg.Addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("http: %v", err)
		}
	}()

	<-ctx.Done()
	log.Println("shutting down…")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
}
