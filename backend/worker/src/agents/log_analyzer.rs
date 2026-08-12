//! Log Analyzer agent: hybrid retrieval (Qdrant dense + full-text) → root
//! cause. Distills multi-gigabyte log corpora into an evidence-backed
//! `RootCauseSummary` and indexes the result back into long-term memory.

use anyhow::Result;
use serde_json::{json, Map, Value};

use crate::context::AgentContext;
use crate::state::{AgentState, RootCauseSummary};

const SYSTEM_PROMPT: &str = r#"You are an expert SRE log analyst. Based ONLY on the evidence below, state the most probable root cause of the incident. Respond ONLY with JSON:
{"hypothesis": str, "confidence": float}
Keep the hypothesis under 120 words."#;

pub async fn run(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let query = build_query(&state.incident);
    let _ = ctx
        .emit_log(&state, &format!("[log_analyzer] hybrid search: {query}"))
        .await;

    let hits = ctx.memory.hybrid_search(&query, 5).await?;
    let evidence: Vec<String> = hits
        .iter()
        .map(|h| h.text.clone())
        .filter(|t| !t.trim().is_empty())
        .collect();
    let _ = ctx
        .emit_log(
            &state,
            &format!("[log_analyzer] retrieved {} evidence spans", evidence.len()),
        )
        .await;

    for hit in hits.iter().take(3) {
        let mut payload = Map::new();
        payload.insert("title".into(), json!("evidence span"));
        payload.insert(
            "detail".into(),
            json!(hit.text.chars().take(400).collect::<String>()),
        );
        payload.insert("memory_id".into(), json!(hit.id));
        let _ = ctx.emit_step(&state, "evidence span", json!(payload)).await;
    }

    let evidence_refs: Vec<String> = hits.iter().take(3).map(|h| h.id.clone()).collect();
    let user_prompt = format!(
        "Incident:\n{}\n\nEvidence:\n{}\n\nRespond with the root cause JSON only.",
        serde_json::to_string_pretty(&state.incident).unwrap_or_default(),
        if evidence.is_empty() {
            "no evidence retrieved — reason from the incident payload".to_string()
        } else {
            evidence.join("\n---\n")
        }
    );

    let (hypothesis, confidence, state) =
        match ctx.complete_json(&state, SYSTEM_PROMPT, &user_prompt).await {
            Ok((data, next)) => {
                let hypothesis = data
                    .get("hypothesis")
                    .and_then(|v| v.as_str())
                    .unwrap_or("root cause could not be determined")
                    .to_string();
                let confidence = data
                    .get("confidence")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.5);
                (hypothesis, confidence, next)
            }
            Err(err) => {
                let _ = ctx
                    .emit_log(
                        &state,
                        &format!("[log_analyzer] llm unavailable, rule-based root cause ({err})"),
                    )
                    .await;
                (rule_hypothesis(&state.incident), 0.3, state)
            }
        };

    let summary = RootCauseSummary {
        hypothesis: hypothesis.clone(),
        evidence: evidence.clone(),
        confidence,
        memory_refs: evidence_refs,
    };
    let _ = ctx
        .emit_log(
            &state,
            &format!(
                "[log_analyzer] root cause: {}",
                hypothesis.chars().take(100).collect::<String>()
            ),
        )
        .await;

    let memory_ref = format!("mem_{}", crate::state::hex16());
    let mut metadata = Map::new();
    metadata.insert("incident_id".into(), json!(state.incident_id));
    metadata.insert("confidence".into(), json!(confidence));
    let _ = ctx
        .memory
        .index_document(&memory_ref, &hypothesis, metadata)
        .await;

    let mut state = state;
    state = state.replace_root_cause(summary);
    Ok(state)
}

fn build_query(incident: &Map<String, Value>) -> String {
    let title = incident.get("title").and_then(|v| v.as_str()).unwrap_or("");
    let message = incident
        .get("message")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let error = incident.get("error").and_then(|v| v.as_str()).unwrap_or("");
    let service = incident
        .get("service")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    format!("{service} {title} {error} {message}")
}

fn rule_hypothesis(incident: &Map<String, Value>) -> String {
    let message = incident
        .get("message")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_lowercase();
    if message.contains("timeout") || message.contains("timed out") {
        "Latency timeout across the affected service; likely a saturated upstream dependency or exhausted connection pool.".to_string()
    } else if message.contains("oom") || message.contains("out of memory") {
        "Memory exhaustion in the workload; check per-container limits and heap configuration."
            .to_string()
    } else if message.contains("connection refused") || message.contains("unreachable") {
        "Downstream dependency unavailable; verify readiness, DNS and network policy between services.".to_string()
    } else {
        "Unclassified incident; inspecting logs and correlated metrics is the next step."
            .to_string()
    }
}
