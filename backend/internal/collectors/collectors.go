// Package collectors normalizes native alert payloads (Datadog webhook,
// Prometheus/Alertmanager, Kubernetes Events) into incident create payloads.
// Pollers for Prometheus and Datadog run in the gateway; the K8s client stays
// optional and is polled from the worker pool when credentials exist.
package collectors

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"auto-sre/backend/internal/config"
)

// Normalize maps a raw provider webhook payload to IncidentCreate-shaped maps.
func Normalize(source string, payload map[string]any) []map[string]any {
	switch source {
	case "datadog":
		return datadogWebhook(payload)
	case "prometheus":
		return prometheusWebhook(payload)
	case "kubernetes":
		return kubernetesWebhook(payload)
	}
	return nil
}

func normalizeSeverity(v string, fallback string) string {
	switch strings.ToLower(v) {
	case "critical", "severe", "fatal", "p1":
		return "critical"
	case "high", "warning", "warn", "error", "p2":
		return "high"
	case "medium", "moderate", "p3":
		return "medium"
	case "low", "info", "p4", "p5", "normal":
		return "low"
	}
	if fallback != "" {
		return fallback
	}
	return "high"
}

func firstTagValue(tags any, key string) string {
	items, ok := tags.([]any)
	if !ok {
		return ""
	}
	for _, item := range items {
		if s, ok := item.(string); ok {
			lower := strings.ToLower(s)
			if strings.HasPrefix(lower, key+":") {
				return strings.SplitN(s, ":", 2)[1]
			}
		}
	}
	return ""
}

func asString(v any) string {
	switch s := v.(type) {
	case string:
		return s
	case json.Number:
		return s.String()
	case float64:
		return strconv.FormatFloat(s, 'f', -1, 64)
	default:
		return fmt.Sprint(v)
	}
}

func asDict(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func datadogWebhook(payload map[string]any) []map[string]any {
	title := asString(payload["title"])
	if title == "" {
		title = asString(payload["event_title"])
	}
	if title == "" {
		title = "Datadog alert"
	}
	return []map[string]any{{
		"source":      "datadog",
		"title":       title,
		"severity":    normalizeSeverity(asString(payload["priority"])+asString(payload["alert_type"]), "high"),
		"service":     firstTagValue(payload["tags"], "service"),
		"error":       asString(payload["alert_type"]),
		"message":     asString(payload["text"]) + asString(payload["body"]),
		"raw_payload": payload,
	}}
}

func prometheusWebhook(payload map[string]any) []map[string]any {
	alerts, _ := payload["alerts"].([]any)
	var out []map[string]any
	for _, a := range alerts {
		alert := asDict(a)
		labels := asDict(alert["labels"])
		annotations := asDict(alert["annotations"])
		name := asString(labels["alertname"])
		if name == "" {
			name = asString(labels["alert"])
		}
		if name == "" {
			name = asString(annotations["summary"])
		}
		if name == "" {
			name = asString(annotations["title"])
		}
		if name == "" {
			name = "Prometheus alert"
		}
		service := asString(labels["service"])
		if service == "" {
			service = asString(labels["job"])
		}
		if service == "" {
			service = asString(labels["namespace"])
		}
		message := asString(annotations["description"])
		if message == "" {
			message = asString(annotations["summary"])
		}
		out = append(out, map[string]any{
			"source":      "prometheus",
			"title":       name,
			"severity":    normalizeSeverity(asString(labels["severity"]), "high"),
			"service":     service,
			"error":       asString(labels["alertname"]),
			"message":     message,
			"raw_payload": alert,
		})
	}
	return out
}

func kubernetesWebhook(payload map[string]any) []map[string]any {
	if strings.ToLower(asString(payload["type"])) == "normal" {
		return nil
	}
	reason := asString(payload["reason"])
	if reason == "" {
		reason = "KubernetesEvent"
	}
	involved := asDict(payload["involvedObject"])
	namespace := asString(payload["namespace"])
	if namespace == "" {
		namespace = asString(involved["namespace"])
	}
	if namespace == "" {
		namespace = "default"
	}
	return []map[string]any{{
		"source":      "kubernetes",
		"title":       namespace + "/" + reason,
		"severity":    normalizeSeverity(asString(payload["type"]), "high"),
		"service":     namespace,
		"error":       reason,
		"message":     asString(payload["message"]),
		"raw_payload": payload,
	}}
}

// PollPrometheus runs the ALERTS query against the Prometheus HTTP API.
func PollPrometheus(ctx context.Context, cfg config.Config) ([]map[string]any, error) {
	if cfg.PrometheusURL == "" {
		return nil, nil
	}
	base := strings.TrimRight(cfg.PrometheusURL, "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		base+"/api/v1/query", nil)
	if err != nil {
		return nil, err
	}
	q := req.URL.Query()
	q.Set("query", cfg.PrometheusQuery)
	req.URL.RawQuery = q.Encode()

	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("prometheus poll status %d", resp.StatusCode)
	}
	var body struct {
		Data struct {
			Result []struct {
				Metric map[string]any `json:"metric"`
				Value  any            `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, series := range body.Data.Result {
		if state, ok := series.Metric["alertstate"].(string); ok && state != "firing" {
			continue
		}
		raw := map[string]any{}
		for k, v := range series.Metric {
			raw[k] = v
		}
		raw["_value"] = series.Value
		name := asString(series.Metric["alertname"])
		if name == "" {
			name = "Prometheus alert"
		}
		service := asString(series.Metric["service"])
		if service == "" {
			service = asString(series.Metric["job"])
		}
		if service == "" {
			service = asString(series.Metric["namespace"])
		}
		out = append(out, map[string]any{
			"source":      "prometheus",
			"title":       name,
			"severity":    normalizeSeverity(asString(series.Metric["severity"]), "high"),
			"service":     service,
			"error":       asString(series.Metric["alertname"]),
			"raw_payload": raw,
		})
	}
	return out, nil
}

// PollDatadog lists monitors in Alert state via the Datadog v1 API.
func PollDatadog(ctx context.Context, cfg config.Config) ([]map[string]any, error) {
	if cfg.DatadogAPIKey == "" || cfg.DatadogAppKey == "" {
		return nil, nil
	}
	base := fmt.Sprintf("https://api.%s", cfg.DatadogSite)
	endpoint, err := url.Parse(base + "/api/v1/monitor")
	if err != nil {
		return nil, err
	}
	q := endpoint.Query()
	if tags := cfg.DatadogMonitorTags; tags != "" {
		q.Set("tags", tags)
	}
	endpoint.RawQuery = q.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("DD-API-KEY", cfg.DatadogAPIKey)
	req.Header.Set("DD-APPLICATION-KEY", cfg.DatadogAppKey)

	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("datadog poll status %d", resp.StatusCode)
	}
	var monitors []map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&monitors); err != nil {
		return nil, err
	}
	var out []map[string]any
	for _, m := range monitors {
		if asString(m["overall_state"]) != "Alert" {
			continue
		}
		name := asString(m["name"])
		if name == "" {
			name = asString(m["query"])
		}
		if name == "" {
			name = "Datadog monitor"
		}
		priority := asString(m["priority"])
		if priority == "" {
			priority = asString(m["priority_str"])
		}
		out = append(out, map[string]any{
			"source":      "datadog",
			"title":       name,
			"severity":    normalizeSeverity(priority, "high"),
			"service":     firstTagValue(m["tags"], "service"),
			"error":       asString(m["type"]),
			"message":     asString(m["message"]),
			"raw_payload": m,
		})
	}
	return out, nil
}
