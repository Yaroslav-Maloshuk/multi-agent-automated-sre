//! Python-free local LLM inference — Candle (CPU) serving a Qwen2 family
//! model behind an OpenAI-compatible HTTP API.
//!
//! Endpoints (OpenAI wire-compatible):
//!   * `GET  /health`              – liveness + loaded model
//!   * `GET  /v1/models`           – model listing
//!   * `POST /v1/chat/completions` – greedy chat generation
//!   * `POST /v1/embeddings`       – mean-pooled, L2-normalised embeddings
//!
//! The engine defaults to a small Instruct model so it can run on plain CPU in
//! a laptop/edge deployment; set `HF_MODEL_ID` (e.g. `Qwen/Qwen2.5-3B-Instruct`)
//! for higher quality. This crate is entirely optional — the workers fall back
//! to OpenAI/Anthropic/LiteLLM providers when it is not running.

mod engine;

use std::sync::{Arc, Mutex};

use anyhow::Context;
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use candle_core::Device;
use engine::{ChatMessage, InferenceEngine};
use serde::Deserialize;
use serde_json::{json, Value};

#[derive(Clone)]
struct AppState {
    engine: Arc<Mutex<InferenceEngine>>,
    model_id: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "auto_sre_inference=info".into()),
        )
        .init();

    let model_id =
        std::env::var("HF_MODEL_ID").unwrap_or_else(|_| "Qwen/Qwen2.5-1.5B-Instruct".to_string());
    let revision = std::env::var("HF_MODEL_REVISION").unwrap_or_else(|_| "main".to_string());
    let port: u16 = std::env::var("PORT")
        .unwrap_or_else(|_| "11434".to_string())
        .parse()?;
    let max_seq_len: usize = std::env::var("MAX_SEQ_LEN")
        .unwrap_or_else(|_| "2048".to_string())
        .parse()?;
    let temperature: f32 = std::env::var("SAMPLING_TEMPERATURE")
        .unwrap_or_else(|_| "0.2".to_string())
        .parse()?;

    tracing::info!(%model_id, %revision, "loading model on CPU (Candle)");
    let device = Device::Cpu;
    let load_model_id = model_id.clone();
    let engine = tokio::task::spawn_blocking(move || {
        InferenceEngine::load(&load_model_id, &revision, &device, max_seq_len, temperature)
    })
    .await
    .context("model load task")??;
    tracing::info!(%model_id, "model ready");

    let state = AppState {
        engine: Arc::new(Mutex::new(engine)),
        model_id,
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(list_models))
        .route("/v1/chat/completions", post(chat_completions))
        .route("/v1/embeddings", post(embeddings))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await?;
    tracing::info!(port, "inference server listening");
    axum::serve(listener, app).await?;
    Ok(())
}

async fn health(State(state): State<AppState>) -> JsonOk {
    JsonOk(json!({
        "status": "ok",
        "model": state.model_id,
        "backend": "candle",
    }))
}

async fn list_models(State(state): State<AppState>) -> JsonOk {
    JsonOk(json!({
        "object": "list",
        "data": [{"id": state.model_id, "object": "model", "owned_by": "auto-sre"}],
    }))
}

#[derive(Debug, Deserialize)]
struct ChatRequest {
    #[allow(dead_code)] // OpenAI wire-compat field
    model: Option<String>,
    messages: Vec<ChatMessage>,
    #[serde(default)]
    max_tokens: Option<usize>,
    #[serde(default)]
    #[allow(dead_code)] // accepted for API parity; generation is greedy
    temperature: Option<f32>,
}

async fn chat_completions(
    State(state): State<AppState>,
    axum::Json(req): axum::Json<ChatRequest>,
) -> Response {
    if req.messages.is_empty() {
        return (StatusCode::BAD_REQUEST, "{\"error\":\"messages required\"}").into_response();
    }
    let max_tokens = req.max_tokens.unwrap_or(256).min(1024);
    let engine = state.engine.clone();
    let model_id = state.model_id.clone();
    match tokio::task::spawn_blocking(move || {
        let mut engine = engine.lock().expect("engine lock");
        engine.generate(&req.messages, max_tokens)
    })
    .await
    {
        Ok(Ok((content, prompt_tokens, completion_tokens))) => JsonOk(json!({
            "id": "chatcmpl-autosre",
            "object": "chat.completion",
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }))
        .into_response(),
        Ok(Err(err)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("{{\"error\":\"{}\"}}", err.to_string().replace('"', "\\\"")),
        )
            .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("{{\"error\":\"join: {err}\"}}"),
        )
            .into_response(),
    }
}

#[derive(Debug, Deserialize)]
struct EmbeddingRequest {
    #[allow(dead_code)] // OpenAI wire-compat field
    model: Option<String>,
    input: Value,
}

async fn embeddings(
    State(state): State<AppState>,
    axum::Json(req): axum::Json<EmbeddingRequest>,
) -> Response {
    let texts: Vec<String> = match &req.input {
        Value::String(s) => vec![s.clone()],
        Value::Array(items) => items
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect(),
        _ => vec![],
    };
    if texts.is_empty() {
        return (StatusCode::BAD_REQUEST, "{\"error\":\"input required\"}").into_response();
    }
    let engine = state.engine.clone();
    let model_id = state.model_id.clone();
    match tokio::task::spawn_blocking(move || {
        let mut engine = engine.lock().expect("engine lock");
        engine.embed(&texts)
    })
    .await
    {
        Ok(Ok((vectors, tokens))) => {
            let mut data = Vec::new();
            for (i, vector) in vectors.iter().enumerate() {
                data.push(json!({"object": "embedding", "index": i, "embedding": vector}));
            }
            JsonOk(json!({
                "object": "list",
                "model": model_id,
                "data": data,
                "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
            }))
            .into_response()
        }
        Ok(Err(err)) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("{{\"error\":\"{}\"}}", err.to_string().replace('"', "\\\"")),
        )
            .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("{{\"error\":\"join: {err}\"}}"),
        )
            .into_response(),
    }
}

/// JSON body responder that also carries `Content-Type: application/json`.
struct JsonOk(Value);

impl IntoResponse for JsonOk {
    fn into_response(self) -> Response {
        let bytes = serde_json::to_vec(&self.0).unwrap_or_else(|_| b"{}".to_vec());
        (
            StatusCode::OK,
            [(axum::http::header::CONTENT_TYPE, "application/json")],
            bytes,
        )
            .into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_request_deserializes_openai_shape() {
        let raw = r#"{"model":"m","messages":[{"role":"user","content":"hi"}],"max_tokens":10}"#;
        let req: ChatRequest = serde_json::from_str(raw).unwrap();
        assert_eq!(req.messages.len(), 1);
        assert_eq!(req.max_tokens, Some(10));
    }

    #[test]
    fn embedding_request_accepts_string_or_array() {
        let a: EmbeddingRequest = serde_json::from_str(r#"{"input":"x"}"#).unwrap();
        assert_eq!(a.input, json!("x"));
        let b: EmbeddingRequest = serde_json::from_str(r#"{"input":["a","b"]}"#).unwrap();
        assert_eq!(b.input.as_array().unwrap().len(), 2);
    }
}
