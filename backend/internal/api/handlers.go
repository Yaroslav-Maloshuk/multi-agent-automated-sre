package api

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"

	"github.com/jackc/pgx/v5"

	"auto-sre/backend/internal/collectors"
	"auto-sre/backend/internal/envelope"
)

type errorResponse struct {
	Detail string `json:"detail"`
	Code   string `json:"code,omitempty"`
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, code, detail string) {
	writeJSON(w, status, errorResponse{Detail: detail, Code: code})
}

func (a *App) handleHealth(w http.ResponseWriter, r *http.Request) {
	deps := map[string]string{"bus": "ok", "postgres": "ok", "qdrant": "ok", "valkey": "ok"}
	if err := a.Bus.Ping(); err != nil {
		deps["bus"] = "unreachable"
	}
	if err := a.Store.Ping(r.Context()); err != nil {
		deps["postgres"] = "unreachable"
	}
	status := "ok"
	for _, v := range deps {
		if v != "ok" {
			status = "degraded"
			break
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":       status,
		"version":      a.Cfg.AppVersion,
		"environment":  a.Cfg.AppEnv,
		"dependencies": deps,
	})
}

func (a *App) handleCreateIncident(w http.ResponseWriter, r *http.Request) {
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "validation_error", "invalid JSON body")
		return
	}
	incidentID := "inc_" + envelope.RandomHex(16)
	payload["id"] = incidentID
	payload["created_at"] = nowISO()
	if _, ok := payload["severity"]; !ok {
		payload["severity"] = "high"
	}
	if _, ok := payload["source"]; !ok {
		payload["source"] = "api"
	}
	if err := a.Store.UpsertIncident(r.Context(), incidentID, payload); err != nil {
		log.Printf("store: create incident failed: %v", err)
		writeError(w, http.StatusInternalServerError, "store_error", "could not persist incident")
		return
	}
	env := envelope.New(envelope.IncidentCreated, envelope.ChannelIncident(incidentID)).
		WithIncident(incidentID).
		WithPayload(map[string]any{
			"incident_id": incidentID,
			"title":       payload["title"],
		})
	_ = a.Bus.PublishEnvelope(env)
	_ = a.Bus.PublishEnvelope(envelope.New(envelope.GraphStatus, envelope.ChannelIncident(incidentID)).
		WithIncident(incidentID).WithPayload(map[string]any{"status": "QUEUED"}))

	writeJSON(w, http.StatusCreated, map[string]any{
		"incident_id": incidentID,
		"incident":    payload,
	})

	if auto, _ := payload["auto_run"].(bool); auto {
		a.dispatchRun(r.Context(), w, incidentID)
	}
}

func (a *App) handleListIncidents(w http.ResponseWriter, r *http.Request) {
	incidents, err := a.Store.ListIncidents(r.Context(), 200)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not list incidents")
		return
	}
	list := make([]map[string]any, 0, len(incidents))
	for _, inc := range incidents {
		id, _ := inc["id"].(string)
		list = append(list, map[string]any{
			"incident_id": id,
			"title":       inc["title"],
			"severity":    inc["severity"],
			"status":      a.LiveStatus(r.Context(), id),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"incidents": list})
}

func (a *App) handleGetIncident(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	incident, err := a.Store.GetIncident(r.Context(), id)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeError(w, http.StatusNotFound, "not_found", "incident not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "store_error", "could not load incident")
		return
	}
	state, err := a.Store.GetState(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not load state")
		return
	}
	if state == nil {
		state = map[string]any{"incident_id": id, "status": "QUEUED"}
	}
	roiRow, err := a.Store.GetRoi(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not load roi")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"incident": incident,
		"state":    state,
		"roi":      roiRow,
	})
}

func (a *App) dispatchRun(ctx context.Context, w http.ResponseWriter, incidentID string) {
	if a.IsRunning(incidentID) {
		writeError(w, http.StatusConflict, "conflict", "incident already has an active run")
		return
	}
	incident, err := a.Store.GetIncident(ctx, incidentID)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeError(w, http.StatusNotFound, "not_found", "incident not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "store_error", "could not load incident")
		return
	}
	if err := a.Bus.PublishRunRequest(envelope.RunRequest{IncidentID: incidentID, Incident: incident}); err != nil {
		writeError(w, http.StatusInternalServerError, "bus_error", "could not dispatch run")
		return
	}
	a.MarkRunning(incidentID)
	writeJSON(w, http.StatusAccepted, map[string]any{
		"incident_id": incidentID,
		"run_id":      "pending",
		"status":      "scheduled",
		"executor":    "nats-jetstream",
	})
}

func (a *App) handleRunIncident(w http.ResponseWriter, r *http.Request) {
	a.dispatchRun(r.Context(), w, r.PathValue("incident_id"))
}

func (a *App) handleResumeIncident(w http.ResponseWriter, r *http.Request) {
	a.dispatchRun(r.Context(), w, r.PathValue("incident_id"))
}

func (a *App) handleReviewIncident(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	var payload struct {
		Action        string            `json:"action"`
		Reason        *string           `json:"reason"`
		PatchRef      *string           `json:"patch_ref"`
		ModifiedFiles map[string]string `json:"modified_files"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeError(w, http.StatusUnprocessableEntity, "validation_error", "invalid JSON body")
		return
	}
	action := envelope.ReviewAction(payload.Action)
	switch action {
	case envelope.ReviewApprove, envelope.ReviewReject, envelope.ReviewModify:
	default:
		writeError(w, http.StatusUnprocessableEntity, "validation_error", "action must be approve|reject|modify")
		return
	}
	patchRef := ""
	if payload.PatchRef != nil {
		patchRef = *payload.PatchRef
	}
	review := envelope.HITLReview{
		IncidentID:    id,
		PatchRef:      patchRef,
		Action:        action,
		Reason:        payload.Reason,
		ModifiedFiles: payload.ModifiedFiles,
	}
	if err := a.Bus.PublishReview(id, review); err != nil {
		writeError(w, http.StatusInternalServerError, "bus_error", "could not deliver review")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"incident_id": id,
		"accepted":    true,
		"action":      payload.Action,
	})
}

func (a *App) handleInterruptIncident(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	_ = a.Bus.PublishControl(id, envelope.ControlMessage{Action: "interrupt"})
	writeJSON(w, http.StatusOK, map[string]any{
		"incident_id": id,
		"status":      "INTERRUPTED",
	})
}

func (a *App) handleListSnapshots(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	snaps, err := a.Store.ListSnapshots(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not list snapshots")
		return
	}
	list := make([]map[string]any, 0, len(snaps))
	for _, s := range snaps {
		list = append(list, map[string]any{
			"ref":        s.Ref,
			"run_id":     s.RunID,
			"step_index": s.StepIndex,
			"node":       s.Node,
			"summary":    s.Summary,
			"status":     s.Status,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"incident_id": id,
		"snapshots":   list,
	})
}

func (a *App) handleGetSnapshot(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	ref := r.PathValue("ref")
	snap, err := a.Store.GetSnapshot(r.Context(), ref)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			writeError(w, http.StatusNotFound, "not_found", "snapshot not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "store_error", "could not load snapshot")
		return
	}
	var blob any
	_ = json.Unmarshal(snap.Blob, &blob)
	writeJSON(w, http.StatusOK, map[string]any{
		"incident_id": id,
		"ref":         ref,
		"snapshot":    blob,
	})
}

func (a *App) handleForkIncident(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("incident_id")
	var payload struct {
		SnapshotRef *string `json:"snapshot_ref"`
		Title       *string `json:"title"`
		AutoRun     *bool   `json:"auto_run"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)

	state, err := a.Store.GetState(r.Context(), id)
	if err != nil || state == nil {
		writeError(w, http.StatusNotFound, "not_found", "incident has no runnable state")
		return
	}
	parent, ok := state["incident"].(map[string]any)
	if !ok {
		parent = state
	}
	title := "Fork of " + strField(parent, "title")
	if title == "" {
		title = "Fork of " + id
	}
	if payload.Title != nil {
		title = *payload.Title
	}
	autoRun := true
	if payload.AutoRun != nil {
		autoRun = *payload.AutoRun
	}
	childPayload := map[string]any{
		"title":       title,
		"severity":    strField(parent, "severity"),
		"service":     parent["service"],
		"error":       parent["error"],
		"message":     parent["message"],
		"target_file": parent["target_file"],
		"files":       parent["files"],
		"source":      "fork",
		"raw_payload": map[string]any{
			"parent_incident_id": id,
			"parent_run_id":      state["run_id"],
			"snapshot_ref":       strField(state, ""),
		},
		"auto_run": autoRun,
	}
	if childPayload["severity"] == "" {
		childPayload["severity"] = "high"
	}
	if s := a.childSnapshotRef(payload.SnapshotRef); s != "" {
		childPayload["raw_payload"].(map[string]any)["snapshot_ref"] = s
	}
	raw, _ := json.Marshal(childPayload)
	var inc map[string]any
	_ = json.Unmarshal(raw, &inc)
	inc["id"] = "inc_" + envelope.RandomHex(16)
	inc["created_at"] = nowISO()
	if err := a.Store.UpsertIncident(r.Context(), inc["id"].(string), inc); err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not create fork")
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"incident_id": inc["id"],
		"incident":    inc,
	})
	if autoRun {
		a.dispatchRun(r.Context(), w, inc["id"].(string))
	}
}

func (a *App) childSnapshotRef(ref *string) string {
	if ref == nil {
		return ""
	}
	return *ref
}

var agentCatalog = []map[string]any{
	{"name": "planner", "label": "Planner", "description": "Decomposes an alert into actionable subtasks", "model_hint": "structured JSON (Instructor/DSPy)", "sandboxed": false, "deps": []string{"alert_ingest"}},
	{"name": "log_analyzer", "label": "Log Analyzer", "description": "Qdrant/LanceDB hybrid retrieval distills logs into a root-cause summary", "model_hint": "embedding + chat", "sandboxed": false, "deps": []string{"planner"}},
	{"name": "code_fixer", "label": "Code Fixer", "description": "Writes source patches inside an isolated sandbox, streams live diffs", "model_hint": "chat", "sandboxed": true, "deps": []string{"planner", "log_analyzer"}},
	{"name": "hitl_gate", "label": "Human Gate", "description": "Blocks the run for a signed approve/reject/modify decision", "model_hint": "none", "sandboxed": false, "deps": []string{"code_fixer"}},
	{"name": "verifier", "label": "Verifier", "description": "Runs pytest + Ruff in the sandbox and drives the self-correction loop", "model_hint": "none (execution)", "sandboxed": true, "deps": []string{"hitl_gate"}},
	{"name": "deploy", "label": "Deploy", "description": "Applies an approved, verified patch to the CD pipeline", "model_hint": "none", "sandboxed": false, "deps": []string{"verifier"}},
}

func (a *App) handleListAgents(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"agents": agentCatalog})
}

func (a *App) handleSandboxStats(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"backend":  "docker",
		"warm":     a.Cfg.SandboxPoolMin,
		"in_use":   0,
		"max_size": a.Cfg.SandboxPoolMax,
	})
}

func (a *App) handleWarmSandboxes(w http.ResponseWriter, r *http.Request) {
	var payload struct {
		Count *int `json:"count"`
	}
	_ = json.NewDecoder(r.Body).Decode(&payload)
	count := 0
	if payload.Count != nil {
		count = *payload.Count
	}
	_ = a.Bus.PublishControl("sandbox", envelope.ControlMessage{Action: "warm", RunID: strconv.Itoa(count)})
	writeJSON(w, http.StatusOK, map[string]any{
		"created": count,
		"warm":    a.Cfg.SandboxPoolMin,
		"in_use":  0,
	})
}

func (a *App) handleROI(w http.ResponseWriter, r *http.Request) {
	summary, err := a.Store.RoiSummary(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not load roi summary")
		return
	}
	history, err := a.Store.RoiHistory(r.Context(), 200)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "store_error", "could not load roi history")
		return
	}
	summary["human_mttr_seconds"] = a.Cfg.HumanMTTRSeconds
	summary["loaded_cost_per_hour"] = a.Cfg.HumanLoadedCostPerHour
	writeJSON(w, http.StatusOK, map[string]any{
		"summary": summary,
		"history": history,
	})
}

func (a *App) handleCollectorWebhook(source string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			writeError(w, http.StatusUnprocessableEntity, "validation_error", "invalid JSON body")
			return
		}
		alerts := collectors.Normalize(source, payload)
		created := 0
		for _, alert := range alerts {
			alert["source"] = source
			a.ingestNormalized(w, r, alert)
			created++
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"source":   source,
			"received": len(alerts),
			"created":  created,
		})
	}
}

func (a *App) ingestNormalized(w http.ResponseWriter, r *http.Request, alert map[string]any) {
	id := "inc_" + envelope.RandomHex(16)
	alert["id"] = id
	alert["created_at"] = nowISO()
	if err := a.Store.UpsertIncident(r.Context(), id, alert); err != nil {
		return
	}
	_ = a.Bus.PublishEnvelope(envelope.New(envelope.IncidentCreated, envelope.ChannelIncident(id)).
		WithIncident(id).WithPayload(map[string]any{"incident_id": id, "title": alert["title"]}))
}

func (a *App) handleCollectorStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"collectors": a.Cfg.CollectorsEnabled,
		"running":    len(a.Cfg.CollectorsEnabled) > 0,
	})
}

func itoa(n int) string { return strconv.Itoa(n) }
