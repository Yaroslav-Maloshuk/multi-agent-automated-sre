//! Planner agent: breaks an alert into actionable subtasks. Uses structured
//! JSON calling when the model is available and degrades to a deterministic,
//! severity-aware rule-based plan.

use anyhow::Result;
use serde_json::{json, Map, Value};

use crate::context::AgentContext;
use crate::state::{AgentState, Subtask, SubtaskStatus};

const SYSTEM_PROMPT: &str = r#"You are a senior SRE planner. Given an infrastructure alert, produce a concise, actionable JSON plan. Respond ONLY with JSON of the shape:
{"subtasks": [{"title": str, "agent": "log_analyzer"|"code_fixer"|"verifier", "detail": str}]}
Between 2 and 5 subtasks."#;

pub async fn run(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let title = state
        .incident
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("untitled");
    let _ = ctx
        .emit_log(&state, &format!("[planner] decomposing alert '{title}'"))
        .await;

    let query = format!(
        "{title} {}",
        state
            .incident
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("")
    );
    let _ = ctx
        .emit_log(
            &state,
            &format!("[planner] retrieving similar past incidents for '{query}'"),
        )
        .await;
    let history: Vec<String> = ctx
        .memory
        .hybrid_search(&query, 3)
        .await
        .unwrap_or_default()
        .iter()
        .map(|h| h.text.clone())
        .filter(|t| !t.trim().is_empty())
        .collect();
    if !history.is_empty() {
        let _ = ctx
            .emit_log(
                &state,
                &format!(
                    "[planner] {} past incident(s) in planning context",
                    history.len()
                ),
            )
            .await;
    }

    let prompt = format!(
        "Incident:\n{}\n\nRelevant past incidents / runbooks:\n{}\n\nProduce the JSON plan only.",
        serde_json::to_string_pretty(&state.incident).unwrap_or_default(),
        if history.is_empty() {
            "none retrieved".to_string()
        } else {
            history.join("\n---\n")
        }
    );
    let plan = match ctx.complete_json(&state, SYSTEM_PROMPT, &prompt).await {
        Ok((data, next)) => {
            let raw = data.get("subtasks").cloned().unwrap_or(Value::Null);
            let subtasks = materialize(&state.incident, Some(&raw));
            let _ = ctx
                .emit_log(
                    &next,
                    &format!("[planner] llm produced {} subtasks", subtasks.len()),
                )
                .await;
            (next, subtasks)
        }
        Err(err) => {
            let _ = ctx
                .emit_log(
                    &state,
                    &format!("[planner] llm unavailable, using rule-based plan ({err})"),
                )
                .await;
            (state.clone(), materialize(&state.incident, None))
        }
    };

    let (state, subtasks) = plan;
    let mut state = state;
    state = state.replace_subtasks(subtasks.clone());
    for (i, task) in subtasks.iter().enumerate() {
        let _ = ctx
            .emit_step(
                &state,
                &format!("subtask {}/{}", i + 1, subtasks.len()),
                json!({"agent": task.agent, "detail": task.detail}),
            )
            .await;
    }
    let _ = ctx
        .emit_log(
            &state,
            &format!("[planner] produced {} subtasks", subtasks.len()),
        )
        .await;
    Ok(state)
}

fn materialize(incident: &Map<String, Value>, plan: Option<&Value>) -> Vec<Subtask> {
    if let Some(plan) = plan {
        if let Some(list) = plan.as_array() {
            let mut out = Vec::new();
            for item in list.iter().filter_map(|v| v.as_object()) {
                let title = item
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("investigate")
                    .to_string();
                let agent = item
                    .get("agent")
                    .and_then(|v| v.as_str())
                    .unwrap_or("log_analyzer")
                    .to_string();
                let detail = item
                    .get("detail")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string();
                if !title.is_empty() {
                    out.push(Subtask {
                        id: format!("st_{}", crate::state::hex16()),
                        title,
                        agent,
                        detail,
                        status: SubtaskStatus::Queued,
                    });
                }
            }
            if !out.is_empty() {
                return out;
            }
        }
    }
    rule_plan(incident)
}

fn rule_plan(incident: &Map<String, Value>) -> Vec<Subtask> {
    let severity = incident
        .get("severity")
        .and_then(|v| v.as_str())
        .unwrap_or("high");
    let mut plan = vec![Subtask {
        id: format!("st_{}", crate::state::hex16()),
        title: "Analyze logs for the failure signature".into(),
        agent: "log_analyzer".into(),
        detail: "scan production logs and traces".into(),
        status: SubtaskStatus::Queued,
    }];
    if incident
        .get("files")
        .map(|v| v.is_object())
        .unwrap_or(false)
    {
        plan.push(Subtask {
            id: format!("st_{}", crate::state::hex16()),
            title: "Propose a source patch".into(),
            agent: "code_fixer".into(),
            detail: "target the affected file in the sandbox".into(),
            status: SubtaskStatus::Queued,
        });
    }
    match severity {
        "critical" | "high" => {
            plan.push(Subtask {
                id: format!("st_{}", crate::state::hex16()),
                title: "Verify the patch under pytest".into(),
                agent: "verifier".into(),
                detail: "self-correction loop".into(),
                status: SubtaskStatus::Queued,
            });
        }
        _ => {}
    }
    plan
}
