//! AgentState — immutable snapshot of an entire incident-resolution run.
//!
//! A faithful port of legacy `app/graph/state.py`. Every graph step produces a
//! new state and publishes it over NATS, which powers the time-travel debugger
//! and the ROI ledger. Serialization matches the Python `to_dict()` wire shape
//! so the React command center renders without changes.

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum GraphStatus {
    Queued,
    Running,
    PendingApproval,
    Completed,
    Failed,
    Cancelled,
    Interrupted,
}

impl GraphStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            GraphStatus::Queued => "QUEUED",
            GraphStatus::Running => "RUNNING",
            GraphStatus::PendingApproval => "PENDING_APPROVAL",
            GraphStatus::Completed => "COMPLETED",
            GraphStatus::Failed => "FAILED",
            GraphStatus::Cancelled => "CANCELLED",
            GraphStatus::Interrupted => "INTERRUPTED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentName {
    AlertIngest,
    Planner,
    LogAnalyzer,
    CodeFixer,
    HitlGate,
    Verifier,
    Deploy,
}

impl AgentName {
    pub fn as_str(self) -> &'static str {
        match self {
            AgentName::AlertIngest => "alert_ingest",
            AgentName::Planner => "planner",
            AgentName::LogAnalyzer => "log_analyzer",
            AgentName::CodeFixer => "code_fixer",
            AgentName::HitlGate => "hitl_gate",
            AgentName::Verifier => "verifier",
            AgentName::Deploy => "deploy",
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SubtaskStatus {
    #[default]
    Queued,
    Running,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Subtask {
    pub id: String,
    pub title: String,
    pub agent: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub status: SubtaskStatus,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RootCauseSummary {
    pub hypothesis: String,
    #[serde(default)]
    pub evidence: Vec<String>,
    pub confidence: f64,
    #[serde(default)]
    pub memory_refs: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PatchFile {
    pub path: String,
    pub old_content: String,
    pub new_content: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum PatchStatus {
    #[default]
    Draft,
    PendingApproval,
    Approved,
    Rejected,
    Modified,
    Applied,
}

impl PatchStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            PatchStatus::Draft => "DRAFT",
            PatchStatus::PendingApproval => "PENDING_APPROVAL",
            PatchStatus::Approved => "APPROVED",
            PatchStatus::Rejected => "REJECTED",
            PatchStatus::Modified => "MODIFIED",
            PatchStatus::Applied => "APPLIED",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Patch {
    pub patch_ref: String,
    pub files: Vec<PatchFile>,
    pub diff: String,
    pub summary: String,
    #[serde(default)]
    pub status: PatchStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub review_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerificationReport {
    pub ok: bool,
    pub output: String,
    #[serde(default)]
    pub failures: Vec<String>,
    pub duration_seconds: f64,
    #[serde(default)]
    pub attempt: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub coverage: Option<f64>,
}

#[derive(Debug, Clone, Default, Copy, Serialize, Deserialize)]
pub struct TokenUsage {
    pub input_tokens: i64,
    pub output_tokens: i64,
}

impl TokenUsage {
    pub fn add(&self, other: &TokenUsage) -> TokenUsage {
        TokenUsage {
            input_tokens: self.input_tokens + other.input_tokens,
            output_tokens: self.output_tokens + other.output_tokens,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphStepRecord {
    pub step_index: usize,
    pub node: String,
    pub started_at: String,
    pub finished_at: String,
    pub status: String,
    pub summary: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub snapshot_ref: Option<String>,
    pub duration_seconds: f64,
}

/// Immutable agent state. `replace`-style updates return a new instance.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentState {
    pub incident_id: String,
    pub run_id: String,
    pub correlation_id: String,
    pub status: GraphStatus,
    pub created_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<String>,
    #[serde(default)]
    pub step_index: usize,
    pub incident: Map<String, Value>,
    #[serde(default)]
    pub subtasks: Vec<Subtask>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub root_cause: Option<RootCauseSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub patch: Option<Patch>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verification: Option<VerificationReport>,
    #[serde(default)]
    pub timeline: Vec<GraphStepRecord>,
    #[serde(default)]
    pub memory_snapshots: Vec<String>,
    #[serde(default)]
    pub token_usage: TokenUsage,
    #[serde(default)]
    pub verifier_attempt: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub verifier_feedback: Option<String>,
    #[serde(default)]
    pub hitl_approved: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl AgentState {
    pub fn new(incident: Map<String, Value>, incident_id: Option<String>) -> Self {
        let id = incident_id.unwrap_or_else(|| {
            incident
                .get("id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("inc_{}", hex16()))
        });
        AgentState {
            incident_id: id,
            run_id: format!("run_{}", hex16()),
            correlation_id: format!("corr_{}", hex16()),
            status: GraphStatus::Queued,
            created_at: now_iso(),
            completed_at: None,
            step_index: 0,
            incident,
            subtasks: Vec::new(),
            root_cause: None,
            patch: None,
            verification: None,
            timeline: Vec::new(),
            memory_snapshots: Vec::new(),
            token_usage: TokenUsage::default(),
            verifier_attempt: 0,
            verifier_feedback: None,
            hitl_approved: false,
            error: None,
        }
    }

    pub fn status(&self, status: GraphStatus) -> AgentState {
        let mut s = self.clone();
        s.status = status;
        s
    }

    pub fn finish(&self, status: GraphStatus) -> AgentState {
        let mut s = self.clone();
        s.status = status;
        s.completed_at = Some(now_iso());
        s
    }

    pub fn add_tokens(&self, usage: &TokenUsage) -> AgentState {
        let mut s = self.clone();
        s.token_usage = s.token_usage.add(usage);
        s
    }

    /// Record a completed graph step into the timeline.
    pub fn record_step(
        &self,
        node: AgentName,
        summary: &str,
        snapshot_ref: Option<String>,
    ) -> AgentState {
        let mut s = self.clone();
        s.step_index += 1;
        s.timeline.push(GraphStepRecord {
            step_index: s.step_index,
            node: node.as_str().to_string(),
            started_at: s
                .completed_at
                .clone()
                .unwrap_or_else(|| s.created_at.clone()),
            finished_at: now_iso(),
            status: s.status.as_str().to_string(),
            summary: summary.to_string(),
            snapshot_ref,
            duration_seconds: 0.0,
        });
        s
    }

    pub fn replace_root_cause(&self, root_cause: RootCauseSummary) -> AgentState {
        let mut s = self.clone();
        s.root_cause = Some(root_cause);
        s
    }

    pub fn replace_patch(&self, patch: Patch) -> AgentState {
        let mut s = self.clone();
        s.patch = Some(patch);
        s
    }

    pub fn replace_subtasks(&self, subtasks: Vec<Subtask>) -> AgentState {
        let mut s = self.clone();
        s.subtasks = subtasks;
        s
    }

    pub fn replace_verification(
        &self,
        verification: VerificationReport,
        attempt: usize,
        feedback: Option<String>,
    ) -> AgentState {
        let mut s = self.clone();
        s.verification = Some(verification);
        s.verifier_attempt = attempt;
        s.verifier_feedback = feedback;
        s
    }

    pub fn with_error(&self, error: String) -> AgentState {
        let mut s = self.clone();
        s.error = Some(error);
        s
    }

    pub fn with_hitl_approved(&self) -> AgentState {
        let mut s = self.clone();
        s.hitl_approved = true;
        s
    }

    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).expect("state serialization")
    }

    pub fn elapsed_seconds(&self) -> f64 {
        let start = parse_iso(&self.created_at);
        let end = self
            .completed_at
            .as_deref()
            .map(parse_iso)
            .unwrap_or_else(now_epoch);
        (end - start).max(0.0)
    }
}

pub fn now_iso() -> String {
    Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Nanos, true)
}

pub fn now_epoch() -> f64 {
    Utc::now().timestamp_millis() as f64 / 1000.0
}

pub fn parse_iso(value: &str) -> f64 {
    chrono::DateTime::parse_from_rfc3339(value)
        .map(|dt| dt.timestamp_millis() as f64 / 1000.0)
        .unwrap_or_else(|_| now_epoch())
}

pub fn hex16() -> String {
    let mut b = [0u8; 8];
    let u = Uuid::new_v4().as_bytes().to_vec();
    b.copy_from_slice(&u[..8]);
    hex_upper(&b)
}

fn hex_upper(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

pub fn env_payload(
    ty: &str,
    channel: String,
    incident_id: Option<&str>,
    run_id: Option<&str>,
    payload: Value,
) -> Value {
    let mut map = Map::new();
    map.insert("version".into(), json!(1));
    map.insert("id".into(), json!(format!("evt_{}", hex16())));
    map.insert("type".into(), json!(ty));
    map.insert("channel".into(), json!(channel));
    if let Some(i) = incident_id {
        map.insert("incident_id".into(), json!(i));
    }
    if let Some(r) = run_id {
        map.insert("run_id".into(), json!(r));
    }
    map.insert("timestamp".into(), json!(now_iso()));
    map.insert("payload".into(), payload);
    Value::Object(map)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_serializes_like_python_to_dict() {
        let mut incident = Map::new();
        incident.insert("title".into(), json!("db timeout"));
        incident.insert("severity".into(), json!("high"));
        let state = AgentState::new(incident, None);
        let v = state.to_value();
        assert_eq!(v["status"], json!("QUEUED"));
        assert_eq!(v["incident"]["severity"], json!("high"));
        assert!(v["incident_id"].as_str().unwrap().starts_with("inc_"));
        assert!(v["run_id"].as_str().unwrap().starts_with("run_"));
        assert_eq!(v["timeline"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn record_step_advances_timeline() {
        let mut incident = Map::new();
        incident.insert("title".into(), json!("x"));
        let s = AgentState::new(incident, None).status(GraphStatus::Running);
        let s = s.record_step(AgentName::Planner, "planned", None);
        assert_eq!(s.step_index, 1);
        assert_eq!(s.timeline.len(), 1);
        assert_eq!(s.timeline[0].node, "planner");
    }
}
