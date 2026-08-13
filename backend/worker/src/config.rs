//! Worker runtime configuration, loaded from the environment.

use std::time::Duration;

#[derive(Debug, Clone)]
pub struct WorkerConfig {
    pub nats_url: String,
    pub events_prefix: String,
    pub run_request: String,
    pub hitl_prefix: String,
    pub ctrl_prefix: String,

    pub llm_provider: String,
    pub openai_api_key: Option<String>,
    pub openai_model: String,
    pub openai_base_url: String,
    pub anthropic_api_key: Option<String>,
    pub anthropic_model: String,
    pub candle_url: String,
    pub candle_model: String,
    pub ai_url: String,
    pub ai_model: String,

    pub embedding_url: Option<String>,
    pub embedding_model: String,

    pub qdrant_url: String,
    pub qdrant_memory_collection: String,
    pub qdrant_snapshot_collection: String,

    pub docker_sandbox_image: String,
    pub sandbox_pool_min: i64,
    pub sandbox_pool_max: i64,
    pub sandbox_cpu_quota: f64,
    pub sandbox_mem_limit: i64,
    pub sandbox_network_mode: String,
    pub sandbox_workdir: String,

    pub graph_max_verifier_attempts: usize,
    pub graph_hitl_auto_approve: bool,
    pub graph_hitl_timeout_seconds: u64,
    pub deploy_webhook_url: Option<String>,

    pub hitl_wait_timeout: Duration,
    pub shutdown_grace: Duration,
}

fn env(key: &str, fallback: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| fallback.to_string())
}

fn env_opt(key: &str) -> Option<String> {
    match std::env::var(key) {
        Ok(v) if !v.is_empty() => Some(v),
        _ => None,
    }
}

fn env_bool(key: &str, fallback: bool) -> bool {
    match std::env::var(key) {
        Ok(v) => match v.to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => true,
            "0" | "false" | "no" | "off" => false,
            _ => fallback,
        },
        _ => fallback,
    }
}

fn env_int(key: &str, fallback: i64) -> i64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(fallback)
}

fn env_float(key: &str, fallback: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(fallback)
}

impl WorkerConfig {
    pub fn load() -> Self {
        let sandbox_mem_limit = match env("SANDBOX_MEM_LIMIT", "512m")
            .to_ascii_lowercase()
            .as_str()
        {
            v if v.ends_with('g') => {
                v.trim_end_matches('g').parse::<i64>().unwrap_or(1) * 1024 * 1024 * 1024
            }
            v if v.ends_with('m') => {
                v.trim_end_matches('m').parse::<i64>().unwrap_or(512) * 1024 * 1024
            }
            v => v.trim().parse().unwrap_or(512 * 1024 * 1024),
        };

        WorkerConfig {
            nats_url: env("NATS_URL", "nats://localhost:4222"),
            events_prefix: env("NATS_EVENTS_PREFIX", "sre.events"),
            run_request: env("NATS_RUN_REQUEST", "sre.run.request"),
            hitl_prefix: env("NATS_HITL_PREFIX", "sre.hitl.review"),
            ctrl_prefix: env("NATS_CTRL_PREFIX", "sre.graph.control"),

            llm_provider: env("LLM_PROVIDER", "local"),
            openai_api_key: env_opt("OPENAI_API_KEY"),
            openai_model: env("OPENAI_MODEL", "gpt-4o"),
            openai_base_url: env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            anthropic_api_key: env_opt("ANTHROPIC_API_KEY"),
            anthropic_model: env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            candle_url: env("CANDLE_URL", "http://localhost:11434"),
            candle_model: env("CANDLE_MODEL", "Qwen/Qwen2.5-3B-Instruct"),
            ai_url: env("AI_URL", "http://localhost:8002/v1"),
            ai_model: env("AI_MODEL", "local/Qwen/Qwen2.5-3B-Instruct"),

            embedding_url: env_opt("EMBEDDING_URL"),
            embedding_model: env("EMBEDDING_MODEL", "text-embedding-3-small"),

            qdrant_url: env("QDRANT_URL", "http://localhost:6333"),
            qdrant_memory_collection: env("QDRANT_MEMORY_COLLECTION", "sre_memory"),
            qdrant_snapshot_collection: env("QDRANT_COLLECTION", "sre_snapshots"),

            docker_sandbox_image: env("DOCKER_SANDBOX_IMAGE", "auto-sre/sandbox:dev"),
            sandbox_pool_min: env_int("SANDBOX_POOL_MIN_WARM", 3),
            sandbox_pool_max: env_int("SANDBOX_POOL_MAX", 5),
            sandbox_cpu_quota: env_float("SANDBOX_CPU_QUOTA", 1.5),
            sandbox_mem_limit,
            sandbox_network_mode: env("SANDBOX_NETWORK_MODE", "none"),
            sandbox_workdir: env("SANDBOX_DEFAULT_WORKDIR", "/workspace"),

            graph_max_verifier_attempts: env_int("GRAPH_MAX_VERIFIER_ATTEMPTS", 3) as usize,
            graph_hitl_auto_approve: env_bool("GRAPH_HITL_AUTO_APPROVE", false),
            graph_hitl_timeout_seconds: env_int("GRAPH_HITL_TIMEOUT_SECONDS", 0) as u64,
            deploy_webhook_url: env_opt("DEPLOY_WEBHOOK_URL"),

            hitl_wait_timeout: Duration::from_secs(0),
            shutdown_grace: Duration::from_secs(10),
        }
    }
}
