//! NATS fabric client for the worker pool. The Rust/Tokio workers publish every
//! envelope to `sre.events.*` (fanned out to the Go gateway over WebSockets),
//! consume runs from a JetStream-backed work-queue, and await signed HITL
//! reviews on per-incident subjects.

use anyhow::Result;
use futures::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use tokio::sync::mpsc;

use crate::state::{env_payload, hex16, now_iso};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope {
    pub version: i64,
    pub id: String,
    #[serde(rename = "type")]
    pub type_: String,
    pub channel: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub correlation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub incident_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub run_id: Option<String>,
    pub timestamp: String,
    pub payload: Map<String, Value>,
}

impl Envelope {
    pub fn new(type_: &str, channel: impl Into<String>) -> Self {
        Envelope {
            version: 1,
            id: format!("evt_{}", hex16()),
            type_: type_.to_string(),
            channel: channel.into(),
            correlation_id: None,
            incident_id: None,
            run_id: None,
            timestamp: now_iso(),
            payload: Map::new(),
        }
    }

    pub fn with_incident(mut self, incident_id: &str) -> Self {
        self.incident_id = Some(incident_id.to_string());
        self
    }

    pub fn with_run(mut self, run_id: &str) -> Self {
        self.run_id = Some(run_id.to_string());
        self
    }

    pub fn with_payload(mut self, payload: Map<String, Value>) -> Self {
        self.payload = payload;
        self
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunRequest {
    pub incident_id: String,
    pub incident: Map<String, Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ReviewRequest {
    pub incident_id: String,
    #[serde(default)]
    pub run_id: Option<String>,
    pub patch_ref: String,
    pub action: String,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub modified_files: Map<String, Value>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ControlMessage {
    pub action: String,
    #[serde(default)]
    pub run_id: String,
}

#[derive(Clone)]
pub struct Bus {
    nc: async_nats::Client,
    events_prefix: String,
    run_request: String,
    hitl_prefix: String,
    ctrl_prefix: String,
}

impl Bus {
    pub async fn connect(url: &str) -> Result<Bus> {
        let nc = async_nats::connect(url).await?;
        Ok(Bus {
            nc,
            events_prefix: "sre.events".to_string(),
            run_request: "sre.run.request".to_string(),
            hitl_prefix: "sre.hitl.review".to_string(),
            ctrl_prefix: "sre.graph.control".to_string(),
        })
    }

    pub async fn publish_event(&self, env: &Envelope) -> Result<()> {
        let subject = format!("{}.{}", self.events_prefix, env.channel);
        self.nc
            .publish(subject, serde_json::to_vec(env)?.into())
            .await?;
        Ok(())
    }

    pub async fn publish_raw(&self, subject: &str, payload: Value) -> Result<()> {
        self.nc
            .publish(subject.to_string(), serde_json::to_vec(&payload)?.into())
            .await?;
        Ok(())
    }

    /// Consume run requests from the worker queue group (NATS load-balances
    /// across the pool). Returns a receiver channel fed by a background task.
    pub async fn subscribe_runs(&self) -> Result<mpsc::Receiver<RunRequest>> {
        let sub = self
            .nc
            .queue_subscribe(self.run_request.clone(), "sre-workers".to_string())
            .await?;
        let (tx, rx) = mpsc::channel(256);
        tokio::spawn(async move {
            let mut inbox = sub;
            while let Some(msg) = inbox.next().await {
                match serde_json::from_slice::<RunRequest>(&msg.payload) {
                    Ok(run) => {
                        if tx.send(run).await.is_err() {
                            break;
                        }
                    }
                    Err(err) => tracing::warn!(%err, "malformed run request"),
                }
            }
        });
        Ok(rx)
    }

    /// Subscribe to graph control signals for one incident and forward
    /// `interrupt` into the provided token.
    pub async fn subscribe_control(
        &self,
        incident_id: &str,
        token: tokio_util::sync::CancellationToken,
    ) -> Result<()> {
        let subject = format!("{}.{}", self.ctrl_prefix, incident_id);
        let mut sub = self.nc.subscribe(subject).await?;
        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                match serde_json::from_slice::<ControlMessage>(&msg.payload) {
                    Ok(ctrl) if ctrl.action == "interrupt" => token.cancel(),
                    _ => {}
                }
            }
        });
        Ok(())
    }

    /// Wait for a signed HITL review on `sre.hitl.review.<incident>`.
    /// Returns None on timeout or interrupt.
    pub async fn subscribe_review(
        &self,
        incident_id: &str,
    ) -> Result<mpsc::Receiver<ReviewRequest>> {
        let subject = format!("{}.{}", self.hitl_prefix, incident_id);
        let mut sub = self.nc.subscribe(subject).await?;
        let (tx, rx) = mpsc::channel(16);
        tokio::spawn(async move {
            while let Some(msg) = sub.next().await {
                match serde_json::from_slice::<ReviewRequest>(&msg.payload) {
                    Ok(rev) => {
                        if tx.send(rev).await.is_err() {
                            break;
                        }
                    }
                    Err(err) => tracing::warn!(%err, "malformed review"),
                }
            }
        });
        Ok(rx)
    }

    pub async fn publish_auto_review(
        &self,
        incident_id: &str,
        patch_ref: &str,
        reason: &str,
    ) -> Result<()> {
        let subject = format!("{}.{}", self.hitl_prefix, incident_id);
        self.publish_raw(
            &subject,
            json!({
                "incident_id": incident_id,
                "patch_ref": patch_ref,
                "action": "approve",
                "reason": reason,
            }),
        )
        .await
    }

    /// Subscribe to an arbitrary subject (used for the shared sandbox-control
    /// wildcard listener).
    pub async fn subscribe_raw(&self, subject: &str) -> Result<async_nats::Subscriber> {
        Ok(self.nc.subscribe(subject.to_string()).await?)
    }

    /// Wildcard over all per-incident control subjects.
    pub fn control_subject_wildcard(&self) -> String {
        format!("{}.>", self.ctrl_prefix)
    }

    pub async fn flush(&self) -> Result<()> {
        self.nc.flush().await?;
        Ok(())
    }
}

pub fn channel_incident(incident_id: &str) -> String {
    format!("incident:{}", incident_id)
}

pub fn channel_run(run_id: &str) -> String {
    format!("run:{}", run_id)
}

pub fn envelope_payload(env: &Envelope) -> Value {
    env_payload(
        &env.type_,
        env.channel.clone(),
        env.incident_id.as_deref(),
        env.run_id.as_deref(),
        Value::Object(env.payload.clone()),
    )
}
