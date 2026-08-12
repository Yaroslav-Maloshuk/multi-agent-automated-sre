// Package config loads runtime settings from the environment. All secrets live
// in the environment / `.env` — never in source code.
package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	AppName    string
	AppEnv     string
	AppVersion string
	Debug      bool
	LogLevel   string
	Addr       string

	NATSUrl          string
	NATSEventsPrefix string
	NATSRunRequest   string
	NATSHITLPrefix   string
	NATSCtrlPrefix   string

	PostgresURL string
	ValkeyURL   string
	QdrantURL   string

	QdrantSnapshotCollection string
	QdrantMemoryCollection   string

	CORSOrigins []string

	LLMProvider     string
	CandleURL       string
	CandleModel     string
	MaxTokensPerRun int64
	TokenPriceIn1K  float64
	TokenPriceOut1K float64

	DockerSandboxImage string
	SandboxPoolMin     int
	SandboxPoolMax     int

	GraphMaxVerifierAttempts int
	GraphHITLAutoApprove     bool
	GraphHITLTimeoutSeconds  int

	CollectorsEnabled     []string
	CollectorPollInterval int
	PrometheusURL         string
	PrometheusQuery       string
	DatadogAPIKey         string
	DatadogAppKey         string
	DatadogSite           string
	DatadogMonitorTags    string
	KubernetesKubeconfig  string
	DeployWebhookURL      string

	HumanMTTRSeconds       int
	HumanLoadedCostPerHour float64

	LangfusePublicKey string
	LangfuseSecretKey string
	LangfuseHost      string

	// Derived limits
	WSHeartbeatInterval time.Duration
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getbool(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return fallback
}

func getint(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

func getfloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}

func splitCSV(v string) []string {
	var out []string
	for _, part := range strings.Split(v, ",") {
		if p := strings.TrimSpace(part); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func Load() Config {
	c := Config{
		AppName:    getenv("APP_NAME", "auto-sre-workspace"),
		AppEnv:     getenv("APP_ENV", "development"),
		AppVersion: getenv("APP_VERSION", "1.0.0"),
		Debug:      getbool("DEBUG", false),
		LogLevel:   getenv("LOG_LEVEL", "INFO"),
		Addr:       getenv("GATEWAY_ADDR", ":8000"),

		NATSUrl:          getenv("NATS_URL", "nats://localhost:4222"),
		NATSEventsPrefix: getenv("NATS_EVENTS_PREFIX", "sre.events"),
		NATSRunRequest:   getenv("NATS_RUN_REQUEST", "sre.run.request"),
		NATSHITLPrefix:   getenv("NATS_HITL_PREFIX", "sre.hitl.review"),
		NATSCtrlPrefix:   getenv("NATS_CTRL_PREFIX", "sre.graph.control"),

		PostgresURL: getenv("POSTGRES_URL", "postgresql://sre:sre@localhost:5432/sre"),
		ValkeyURL:   getenv("VALKEY_URL", "redis://localhost:6379/0"),
		QdrantURL:   getenv("QDRANT_URL", "http://localhost:6333"),

		QdrantSnapshotCollection: getenv("QDRANT_COLLECTION", "sre_snapshots"),
		QdrantMemoryCollection:   getenv("QDRANT_MEMORY_COLLECTION", "sre_memory"),

		CORSOrigins: splitCSV(getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:3000")),

		LLMProvider:     getenv("LLM_PROVIDER", "local"),
		CandleURL:       getenv("CANDLE_URL", "http://localhost:11434"),
		CandleModel:     getenv("CANDLE_MODEL", "Qwen/Qwen2.5-3B-Instruct"),
		MaxTokensPerRun: int64(getint("MAX_TOKENS_PER_RUN", 200000)),
		TokenPriceIn1K:  getfloat("TOKEN_PRICE_PER_1K_IN", 0.005),
		TokenPriceOut1K: getfloat("TOKEN_PRICE_PER_1K_OUT", 0.015),

		DockerSandboxImage: getenv("DOCKER_SANDBOX_IMAGE", "auto-sre/sandbox:dev"),
		SandboxPoolMin:     getint("SANDBOX_POOL_MIN_WARM", 3),
		SandboxPoolMax:     getint("SANDBOX_POOL_MAX", 5),

		GraphMaxVerifierAttempts: getint("GRAPH_MAX_VERIFIER_ATTEMPTS", 3),
		GraphHITLAutoApprove:     getbool("GRAPH_HITL_AUTO_APPROVE", false),
		GraphHITLTimeoutSeconds:  getint("GRAPH_HITL_TIMEOUT_SECONDS", 0),

		CollectorsEnabled:     splitCSV(getenv("COLLECTORS_ENABLED", "")),
		CollectorPollInterval: getint("COLLECTOR_POLL_INTERVAL", 30),
		PrometheusURL:         getenv("PROMETHEUS_URL", ""),
		PrometheusQuery:       getenv("PROMETHEUS_QUERY", "ALERTS"),
		DatadogAPIKey:         getenv("DATADOG_API_KEY", ""),
		DatadogAppKey:         getenv("DATADOG_APP_KEY", ""),
		DatadogSite:           getenv("DATADOG_SITE", "datadoghq.com"),
		DatadogMonitorTags:    getenv("DATADOG_MONITOR_TAGS", ""),
		KubernetesKubeconfig:  getenv("KUBERNETES_KUBECONFIG", ""),
		DeployWebhookURL:      getenv("DEPLOY_WEBHOOK_URL", ""),

		HumanMTTRSeconds:       getint("HUMAN_MTTR_SECONDS", 35*60),
		HumanLoadedCostPerHour: getfloat("HUMAN_LOADED_COST_PER_HOUR", 150.0),

		LangfusePublicKey: getenv("LANGFUSE_PUBLIC_KEY", ""),
		LangfuseSecretKey: getenv("LANGFUSE_SECRET_KEY", ""),
		LangfuseHost:      getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
	}
	c.WSHeartbeatInterval = time.Duration(getint("WS_HEARTBEAT_INTERVAL", 30)) * time.Second
	return c
}
