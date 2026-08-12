package api

import (
	"context"
	"net/http"
)

// handleDashboard renders the server-side Templ + HTMX command center.
func (a *App) handleDashboard(w http.ResponseWriter, r *http.Request) {
	component := DashboardPage(a.dashboardRows(r.Context()))
	if err := component.Render(r.Context(), w); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

// handleDashboardIncidents renders just the incidents <tbody> for HTMX swaps.
func (a *App) handleDashboardIncidents(w http.ResponseWriter, r *http.Request) {
	component := IncidentsFragment(a.dashboardRows(r.Context()))
	if err := component.Render(r.Context(), w); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func (a *App) dashboardRows(ctx context.Context) []dashRow {
	if a.Store == nil {
		return nil
	}
	items, err := a.Store.ListIncidents(ctx, 25)
	if err != nil {
		return nil
	}
	rows := make([]dashRow, 0, len(items))
	for _, it := range items {
		rows = append(rows, dashRow{
			ID:       stringOf(it["incident_id"]),
			Status:   stringOf(it["status"]),
			Severity: stringOf(it["severity"]),
		})
	}
	return rows
}

func stringOf(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// badgeClass maps an incident severity to a dashboard badge variant.
func badgeClass(severity string) string {
	switch severity {
	case "low", "medium", "ok":
		return "ok"
	case "high", "critical":
		return "err"
	default:
		return "warn"
	}
}
