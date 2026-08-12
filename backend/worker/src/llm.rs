//! Provider-agnostic LLM client with structured JSON calling.
//!
//! Mirrors the layered stack: `Router` selects a provider (OpenAI-compatible
//! gateway / Candle local / Anthropic / LiteLLM), and `complete_json` performs
//! Outlines/Instructor-style constrained JSON extraction with a graceful
//! degradation path — the engine never stalls on a malformed model response.

use anyhow::{anyhow, Result};
use regex::Regex;
use serde_json::{json, Map, Value};
use std::time::Duration;

use crate::config::WorkerConfig;

#[derive(Debug, Clone, Default)]
pub struct LlmResponse {
    pub text: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
}

/// Rough token estimate (4 chars / token) used when the provider does not
/// report usage — keeps the ROI ledger honest without an extra model call.
pub fn estimate_tokens(text: &str) -> i64 {
    ((text.chars().count() / 4).max(1)) as i64
}

/// Instructor/Outlines-style structured extraction. Strips markdown fences and
/// falls back to the outermost JSON object in the response.
pub fn extract_json(text: &str) -> Result<Value> {
    let trimmed = text.trim();
    let fence = Regex::new(r"```(?:json)?\s*(.*?)\s*```").map_err(|_| anyhow!("invalid regex"))?;
    if let Some(caps) = fence.captures(trimmed) {
        if let Some(group) = caps.get(1) {
            if let Ok(v) = serde_json::from_str::<Value>(group.as_str()) {
                return Ok(v);
            }
        }
    }
    if let Ok(v) = serde_json::from_str::<Value>(trimmed) {
        return Ok(v);
    }
    if let (Some(start), Some(end)) = (trimmed.find('{'), trimmed.rfind('}')) {
        if end > start {
            let slice = &trimmed[start..=end];
            if let Ok(v) = serde_json::from_str::<Value>(slice) {
                return Ok(v);
            }
        }
    }
    Err(anyhow!(
        "model returned malformed JSON: {}",
        &trimmed[..trimmed.len().min(200)]
    ))
}

#[async_trait::async_trait]
pub trait Llm: Send + Sync {
    async fn complete(&self, system: &str, user: &str) -> Result<LlmResponse>;
}

/// OpenAI-compatible chat completions (works for OpenAI, LiteLLM gateways,
/// vLLM and the Python-free Candle inference service).
#[derive(Clone)]
pub struct OpenAiCompatible {
    http: reqwest::Client,
    base_url: String,
    model: String,
    api_key: Option<String>,
    json_mode: bool,
}

impl OpenAiCompatible {
    pub fn new(base_url: &str, model: &str, api_key: Option<String>, json_mode: bool) -> Self {
        OpenAiCompatible {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(180))
                .connect_timeout(Duration::from_secs(10))
                .build()
                .expect("http client"),
            base_url: base_url.trim_end_matches('/').to_string(),
            model: model.to_string(),
            api_key,
            json_mode,
        }
    }
}

#[async_trait::async_trait]
impl Llm for OpenAiCompatible {
    async fn complete(&self, system: &str, user: &str) -> Result<LlmResponse> {
        let mut body = json!({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "max_tokens": 8192,
        });
        if self.json_mode {
            body["response_format"] = json!({"type": "json_object"});
        }
        let mut req = self
            .http
            .post(format!("{}/chat/completions", self.base_url))
            .json(&body);
        if let Some(key) = &self.api_key {
            req = req.bearer_auth(key);
        }
        let resp = req.send().await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow!(
                "llm {} error {}: {}",
                self.model,
                status,
                &text[..text.len().min(300)]
            ));
        }
        let data: Value = resp.json().await?;
        let text = data["choices"][0]["message"]["content"]
            .as_str()
            .unwrap_or_default()
            .to_string();
        if text.is_empty() {
            return Err(anyhow!("llm {} returned empty content", self.model));
        }
        let input_tokens = data["usage"]["prompt_tokens"]
            .as_i64()
            .unwrap_or_else(|| estimate_tokens(&format!("{system}\n{user}")));
        let output_tokens = data["usage"]["completion_tokens"]
            .as_i64()
            .unwrap_or_else(|| estimate_tokens(&text));
        Ok(LlmResponse {
            text,
            input_tokens,
            output_tokens,
        })
    }
}

/// Anthropic Messages API.
#[derive(Clone)]
pub struct AnthropicLlm {
    http: reqwest::Client,
    api_key: String,
    model: String,
}

#[async_trait::async_trait]
impl Llm for AnthropicLlm {
    async fn complete(&self, system: &str, user: &str) -> Result<LlmResponse> {
        let body = json!({
            "model": self.model,
            "max_tokens": 8192,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        });
        let resp = self
            .http
            .post("https://api.anthropic.com/v1/messages")
            .header("x-api-key", &self.api_key)
            .header("anthropic-version", "2023-06-01")
            .json(&body)
            .send()
            .await?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow!(
                "anthropic error {}: {}",
                status,
                &text[..text.len().min(300)]
            ));
        }
        let data: Value = resp.json().await?;
        let text = data["content"]
            .as_array()
            .and_then(|arr| arr.iter().find(|b| b["type"] == "text"))
            .and_then(|b| b["text"].as_str())
            .unwrap_or_default()
            .to_string();
        let input_tokens = data["usage"]["input_tokens"]
            .as_i64()
            .unwrap_or_else(|| estimate_tokens(system));
        let output_tokens = data["usage"]["output_tokens"]
            .as_i64()
            .unwrap_or_else(|| estimate_tokens(&text));
        Ok(LlmResponse {
            text,
            input_tokens,
            output_tokens,
        })
    }
}

/// A provider that always fails fast — used when a provider is unconfigured,
/// so agents degrade to deterministic rule-based fallbacks.
#[derive(Clone)]
pub struct UnavailableLlm {
    reason: String,
}

#[async_trait::async_trait]
impl Llm for UnavailableLlm {
    async fn complete(&self, _system: &str, _user: &str) -> Result<LlmResponse> {
        Err(anyhow!("llm unavailable: {}", self.reason))
    }
}

pub fn build_router(cfg: &WorkerConfig) -> Box<dyn Llm> {
    match cfg.llm_provider.as_str() {
        "anthropic" => {
            if let Some(key) = &cfg.anthropic_api_key {
                return Box::new(AnthropicLlm {
                    http: reqwest::Client::builder()
                        .timeout(Duration::from_secs(180))
                        .build()
                        .expect("http client"),
                    api_key: key.clone(),
                    model: cfg.anthropic_model.clone(),
                });
            }
            Box::new(UnavailableLlm {
                reason: "ANTHROPIC_API_KEY not set".into(),
            })
        }
        "openai" => {
            if cfg.openai_api_key.is_some() {
                return Box::new(OpenAiCompatible::new(
                    &cfg.openai_base_url,
                    &cfg.openai_model,
                    cfg.openai_api_key.clone(),
                    true,
                ));
            }
            Box::new(UnavailableLlm {
                reason: "OPENAI_API_KEY not set".into(),
            })
        }
        _ => Box::new(OpenAiCompatible::new(
            &cfg.candle_url,
            &cfg.candle_model,
            None,
            true,
        )),
    }
}

pub fn build_embedding_fn(cfg: &WorkerConfig) -> Option<EmbeddingFn> {
    let url = cfg.embedding_url.clone()?;
    let model = cfg.embedding_model.clone();
    let http = reqwest::Client::builder()
        .timeout(Duration::from_secs(60))
        .build()
        .ok()?;
    Some(Box::new(move |texts: Vec<String>| {
        let http = http.clone();
        let url = url.clone();
        let model = model.clone();
        Box::pin(async move {
            let resp = http
                .post(format!("{}/embeddings", url.trim_end_matches('/')))
                .json(&json!({"model": model, "input": texts}))
                .send()
                .await?;
            if !resp.status().is_success() {
                return Err(anyhow!("embedding error {}", resp.status()));
            }
            let data: Value = resp.json().await?;
            let mut out = Vec::with_capacity(texts.len());
            for item in data["data"].as_array().unwrap_or(&vec![]) {
                let vector = item["embedding"]
                    .as_array()
                    .map(|v| v.iter().filter_map(|x| x.as_f64()).collect::<Vec<f64>>())
                    .unwrap_or_default();
                out.push(vector);
            }
            Ok(out)
        })
    }))
}

pub type EmbeddingFn = Box<
    dyn Fn(
            Vec<String>,
        )
            -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<Vec<Vec<f64>>>> + Send>>
        + Send
        + Sync,
>;

pub fn complete_json_value(value: &Value) -> Result<Map<String, Value>> {
    match extract_json(value.as_str().unwrap_or_default())? {
        Value::Object(map) => Ok(map),
        _ => Err(anyhow!("expected JSON object")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_json_from_fenced_response() {
        let raw = "Sure! Here you go:\n```json\n{\"subtasks\": [{\"title\": \"x\"}]}\n```";
        let v = extract_json(raw).unwrap();
        assert_eq!(v["subtasks"][0]["title"], json!("x"));
    }

    #[test]
    fn extracts_embedded_json_object() {
        let raw = "prefix {\"a\": 1} suffix";
        let v = extract_json(raw).unwrap();
        assert_eq!(v["a"], json!(1));
    }

    #[test]
    fn rejects_non_json() {
        assert!(extract_json("no json here").is_err());
    }
}
