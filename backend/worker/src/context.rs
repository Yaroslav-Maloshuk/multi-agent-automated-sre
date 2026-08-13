//! AgentContext: the shared toolbox handed to every graph node — NATS bus,
//! provider-agnostic LLM client, Qdrant memory, and the shared Docker sandbox
//! pool. Mirrors legacy `app/graph/context.py`.

use std::sync::Arc;
use tokio::sync::Mutex;

use anyhow::Result;
use serde_json::{json, Map, Value};

use crate::bus::{channel_incident, Bus};
use crate::config::WorkerConfig;
use crate::llm::Llm;
use crate::memory::Memory;
use crate::sandbox::SandboxPool;
use crate::state::{AgentState, TokenUsage};

pub const EVENT_AGENT_STARTED: &str = "agent.started";
pub const EVENT_AGENT_STEP: &str = "agent.step";
pub const EVENT_AGENT_LOG: &str = "agent.log";
pub const EVENT_AGENT_TOKEN_USAGE: &str = "agent.token_usage";
pub const EVENT_PATCH_TYPING: &str = "patch.typing";
pub const EVENT_PATCH_DIFF: &str = "patch.diff";
pub const EVENT_PATCH_GENERATED: &str = "patch.generated";
pub const EVENT_PATCH_APPLIED: &str = "patch.applied";
pub const EVENT_SANDBOX_ACQUIRED: &str = "sandbox.acquired";
pub const EVENT_SANDBOX_RELEASED: &str = "sandbox.released";
pub const EVENT_MEMORY_SNAPSHOT: &str = "memory.snapshot";
pub const EVENT_PENDING_APPROVAL: &str = "hitl.pending_approval";
pub const EVENT_REVIEW_DECIDED: &str = "hitl.review_decided";

#[derive(Clone)]
pub struct AgentContext {
    pub bus: Bus,
    pub llm: Arc<dyn Llm>,
    pub memory: Arc<dyn Memory>,
    pub pool: Arc<Mutex<SandboxPool>>,
    pub cfg: WorkerConfig,
}

impl AgentContext {
    pub async fn emit(&self, env: &crate::bus::Envelope) -> Result<()> {
        self.bus.publish_event(env).await
    }

    pub(crate) fn base(&self, state: &AgentState, type_: &str) -> crate::bus::Envelope {
        crate::bus::Envelope::new(type_, channel_incident(&state.incident_id))
            .with_incident(&state.incident_id)
            .with_run(&state.run_id)
    }

    pub async fn emit_log(&self, state: &AgentState, text: &str) -> Result<()> {
        self.emit(
            &self.base(state, EVENT_AGENT_LOG).with_payload(
                json!({"text": text})
                    .as_object()
                    .cloned()
                    .unwrap_or_default(),
            ),
        )
        .await
    }

    pub async fn emit_step(&self, state: &AgentState, title: &str, detail: Value) -> Result<()> {
        let mut payload = Map::new();
        payload.insert("title".into(), json!(title));
        payload.insert("detail".into(), detail);
        self.emit(&self.base(state, EVENT_AGENT_STEP).with_payload(payload))
            .await
    }

    pub async fn emit_tokens(
        &self,
        state: &AgentState,
        input_tokens: i64,
        output_tokens: i64,
    ) -> Result<()> {
        let mut payload = Map::new();
        payload.insert("input_tokens".into(), json!(input_tokens));
        payload.insert("output_tokens".into(), json!(output_tokens));
        self.emit(
            &self
                .base(state, EVENT_AGENT_TOKEN_USAGE)
                .with_payload(payload),
        )
        .await
    }

    /// Single chat round-trip with token accounting and telemetry.
    pub async fn complete(
        &self,
        state: &AgentState,
        system: &str,
        user: &str,
    ) -> Result<(String, AgentState)> {
        let resp = self.llm.complete(system, user).await?;
        let next = state.add_tokens(&TokenUsage {
            input_tokens: resp.input_tokens,
            output_tokens: resp.output_tokens,
        });
        let _ = self
            .emit_tokens(&next, resp.input_tokens, resp.output_tokens)
            .await;
        let _ = self
            .emit_log(
                &next,
                &format!("llm round-trip ({} tokens out)", resp.output_tokens),
            )
            .await;
        Ok((resp.text, next))
    }

    /// Structured JSON completion (Instructor/Outlines-style extraction).
    pub async fn complete_json(
        &self,
        state: &AgentState,
        system: &str,
        user: &str,
    ) -> Result<(Map<String, Value>, AgentState)> {
        let resp = self.llm.complete(system, user).await?;
        let next = state.add_tokens(&TokenUsage {
            input_tokens: resp.input_tokens,
            output_tokens: resp.output_tokens,
        });
        let _ = self
            .emit_tokens(&next, resp.input_tokens, resp.output_tokens)
            .await;
        let _ = self
            .emit_log(
                &next,
                &format!("parsed json plan ({} tokens out)", resp.output_tokens),
            )
            .await;
        let value = crate::llm::extract_json(&resp.text)?;
        match value {
            Value::Object(map) => Ok((map, next)),
            _ => Err(anyhow::anyhow!("model returned non-object JSON")),
        }
    }
}
