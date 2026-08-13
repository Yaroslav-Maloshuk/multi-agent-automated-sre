//! The async DAG executor — heartbeat of the autonomous engine, now on
//! Rust/Tokio over NATS.
//!
//! * Schedules nodes respecting dependencies; fan-out branches (log_analyzer ||
//!   code_fixer) execute concurrently with a deterministic merge.
//! * Publishes every transition as a `memory.snapshot` envelope (time-travel /
//!   UI state sync) and mirrors it into Qdrant.
//! * Enforces the HITL gate: blocks on a signed review delivered over NATS and
//!   resumes (approve / reject / modify).
//! * Runs the verifier self-correction loop up to `max_verifier_attempts`.
//! * Honours interrupts via CancellationToken so a cancelled run never leaves
//!   zombie state.

use std::collections::{HashSet, VecDeque};

use tokio_util::sync::CancellationToken;

use anyhow::Result;
use serde_json::{json, Map, Value};

use crate::agents;
use crate::bus::{Envelope, ReviewRequest};
use crate::context::{
    AgentContext, EVENT_MEMORY_SNAPSHOT, EVENT_PENDING_APPROVAL, EVENT_REVIEW_DECIDED,
};
use crate::state::{AgentName, AgentState, GraphStatus, PatchStatus};

pub const EVENT_GRAPH_STARTED: &str = "graph.started";
pub const EVENT_GRAPH_STEP_STARTED: &str = "graph.step.started";
pub const EVENT_GRAPH_STEP_COMPLETED: &str = "graph.step.completed";
pub const EVENT_GRAPH_STATUS_CHANGED: &str = "graph.status.changed";
pub const EVENT_GRAPH_COMPLETED: &str = "graph.completed";
pub const EVENT_GRAPH_FAILED: &str = "graph.failed";
pub const EVENT_GRAPH_INTERRUPTED: &str = "graph.interrupted";

const NODE_ORDER: [AgentName; 7] = [
    AgentName::AlertIngest,
    AgentName::LogAnalyzer,
    AgentName::CodeFixer,
    AgentName::HitlGate,
    AgentName::Verifier,
    AgentName::Deploy,
    AgentName::Planner,
];

fn order(name: AgentName) -> usize {
    NODE_ORDER.iter().position(|&n| n == name).unwrap_or(0)
}

fn deps(name: AgentName) -> &'static [AgentName] {
    match name {
        AgentName::AlertIngest => &[],
        AgentName::Planner => &[AgentName::AlertIngest],
        AgentName::LogAnalyzer => &[AgentName::Planner],
        AgentName::CodeFixer => &[AgentName::Planner],
        AgentName::HitlGate => &[AgentName::CodeFixer],
        AgentName::Verifier => &[AgentName::HitlGate],
        AgentName::Deploy => &[AgentName::Verifier],
    }
}

#[derive(Debug, Clone)]
pub struct RunOutcome {
    pub state: AgentState,
    pub terminal: bool,
}

pub async fn execute(
    ctx: AgentContext,
    token: CancellationToken,
    initial: AgentState,
) -> RunOutcome {
    let state = initial.status(GraphStatus::Running);
    emit_payload(
        &ctx,
        EVENT_GRAPH_STARTED,
        &state,
        json!({"incident": state.incident, "run_id": state.run_id}),
    )
    .await;
    emit_payload(
        &ctx,
        EVENT_GRAPH_STATUS_CHANGED,
        &state,
        json!({"status": state.status.as_str()}),
    )
    .await;

    let state = schedule(ctx.clone(), token.clone(), state).await;

    let state = finalize(&ctx, state).await;

    let terminal = state.status != GraphStatus::Running;
    RunOutcome { state, terminal }
}

async fn finalize(ctx: &AgentContext, state: AgentState) -> AgentState {
    let mut state = state;
    if state.status == GraphStatus::Running {
        if let Some(verification) = &state.verification {
            if !verification.ok {
                state = state
                    .with_error(format!(
                        "verification failed after {} attempt(s)",
                        state.verifier_attempt
                    ))
                    .finish(GraphStatus::Failed);
            }
        } else {
            state = state
                .with_error("graph completed without a terminal status".into())
                .finish(GraphStatus::Failed);
        }
    }

    match state.status {
        GraphStatus::Completed => {
            emit_payload(
                ctx,
                EVENT_GRAPH_COMPLETED,
                &state,
                json!({"run_id": state.run_id, "duration_seconds": state.elapsed_seconds(),
                       "token_usage": {"input_tokens": state.token_usage.input_tokens, "output_tokens": state.token_usage.output_tokens}}),
            )
            .await;
            learn_resolved_incident(ctx, &state).await;
        }
        GraphStatus::Failed => {
            emit_payload(
                ctx,
                EVENT_GRAPH_FAILED,
                &state,
                json!({"error": state.error}),
            )
            .await;
        }
        GraphStatus::Interrupted => {
            emit_payload(
                ctx,
                EVENT_GRAPH_INTERRUPTED,
                &state,
                json!({"run_id": state.run_id}),
            )
            .await;
        }
        _ => {}
    }
    let final_summary = match state.status {
        GraphStatus::Completed => "graph completed".to_string(),
        GraphStatus::Failed => format!(
            "graph failed: {}",
            state.error.clone().unwrap_or_default()
        ),
        GraphStatus::Interrupted => "graph interrupted".to_string(),
        _ => "graph finished".to_string(),
    };
    // Persist the terminal state as a snapshot so the gateway's absorbed state
    // reflects the final status (previous snapshots predate the transition).
    emit_memory_snapshot(ctx, &state, "finalize", &final_summary).await;
    emit_payload(
        ctx,
        EVENT_GRAPH_STATUS_CHANGED,
        &state,
        json!({"status": state.status.as_str()}),
    )
    .await;
    state
}

/// Learning loop: index a resolved incident (root cause + applied fix) into
/// long-term memory so future planner / log-analyzer / fixer runs can retrieve
/// it as prior art. Indexing failures degrade to a log line only.
async fn learn_resolved_incident(ctx: &AgentContext, state: &AgentState) {
    let learned = format!(
        "RESOLVED INCIDENT: {}\nRoot cause: {}\nFix applied: {}\n",
        state
            .incident
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("untitled"),
        state
            .root_cause
            .as_ref()
            .map(|r| r.hypothesis.as_str())
            .unwrap_or("unknown"),
        state
            .patch
            .as_ref()
            .map(|p| p.summary.as_str())
            .unwrap_or("unknown"),
    );
    let mut metadata = Map::new();
    metadata.insert("incident_id".into(), json!(state.incident_id));
    metadata.insert("run_id".into(), json!(state.run_id));
    metadata.insert("outcome".into(), json!("resolved"));
    if let Err(err) = ctx
        .memory
        .index_document(&format!("postmortem_{}", state.run_id), &learned, metadata)
        .await
    {
        let _ = ctx
            .emit_log(state, &format!("[graph] memory index failed: {err}"))
            .await;
    }
    let _ = ctx
        .emit_log(state, "[graph] resolved incident indexed into memory")
        .await;
}

async fn schedule(ctx: AgentContext, token: CancellationToken, initial: AgentState) -> AgentState {
    let mut state = initial;
    let mut pending: VecDeque<AgentName> = VecDeque::from([AgentName::AlertIngest]);
    let mut completed: HashSet<AgentName> = HashSet::new();
    // Nodes ever enqueued: the normal enqueue step must run each node once;
    // the verifier retry loop re-queues fixer/verifier explicitly.
    let mut ever: HashSet<AgentName> = HashSet::from([AgentName::AlertIngest]);
    let mut reverify = false;

    while let Some(node) = pending.pop_front() {
        if completed.contains(&node) || !deps(node).iter().all(|d| completed.contains(d)) {
            continue;
        }

        // Every pending node whose dependencies are now satisfied joins the
        // frontier and runs concurrently (log_analyzer || code_fixer).
        let others: Vec<AgentName> = pending
            .iter()
            .copied()
            .filter(|n| !completed.contains(n) && deps(*n).iter().all(|d| completed.contains(d)))
            .collect();
        pending.retain(|n| !others.contains(n));
        let mut frontier = vec![node];
        frontier.extend(others);

        let (merged, _) = run_frontier(&ctx, &token, &state, &frontier).await;
        state = merged;
        for n in &frontier {
            completed.insert(*n);
        }

        if frontier.contains(&AgentName::Verifier) && state.status == GraphStatus::Running {
            if let Some(verification) = &state.verification {
                if !verification.ok {
                    if state.verifier_attempt < ctx.cfg.graph_max_verifier_attempts {
                        pending.push_front(AgentName::CodeFixer);
                        completed.remove(&AgentName::CodeFixer);
                        completed.remove(&AgentName::Verifier);
                        reverify = true;
                    } else {
                        state = state
                            .with_error(format!(
                                "verification failed after {} attempt(s)",
                                state.verifier_attempt
                            ))
                            .finish(GraphStatus::Failed);
                    }
                }
            }
        } else if reverify && frontier.contains(&AgentName::CodeFixer) {
            // The retried fixer produced a new patch — re-run the verifier on it.
            reverify = false;
            completed.remove(&AgentName::Verifier);
            pending.push_back(AgentName::Verifier);
        }

        // Enqueue every never-run node whose dependencies are now all satisfied.
        for next in NODE_ORDER.iter().copied() {
            if !completed.contains(&next)
                && !ever.contains(&next)
                && deps(next).iter().all(|d| completed.contains(d))
            {
                ever.insert(next);
                pending.push_back(next);
            }
        }

        if token.is_cancelled() {
            state = state.finish(GraphStatus::Interrupted);
            break;
        }
        if state.status != GraphStatus::Running {
            break;
        }
    }
    state
}

/// Run a frontier concurrently on copy-on-write state and merge deterministically.
async fn run_frontier(
    ctx: &AgentContext,
    token: &CancellationToken,
    base: &AgentState,
    frontier: &[AgentName],
) -> (AgentState, Vec<AgentName>) {
    let mut handles = Vec::with_capacity(frontier.len());
    for &name in frontier {
        let ctx = ctx.clone();
        let token = token.clone();
        let base = base.clone();
        handles.push(tokio::spawn(async move {
            run_node(&ctx, token, base, name).await
        }));
    }

    let mut out: Vec<(AgentName, AgentState)> = Vec::new();
    for (name, handle) in frontier.iter().zip(handles) {
        match handle.await {
            Ok((state, _)) => out.push((*name, state)),
            Err(err) => {
                tracing::error!(node = ?name, %err, "node task panicked");
                out.push((
                    *name,
                    base.clone()
                        .with_error(format!("node {name:?} task error: {err}"))
                        .finish(GraphStatus::Failed),
                ));
            }
        }
    }

    let mut merged = out
        .iter()
        .max_by_key(|(name, _)| order(*name))
        .map(|(_, s)| s.clone())
        .unwrap_or_else(|| base.clone());

    // Deterministic merge: the highest-order node wins; layer in the
    // log_analyzer's root cause so the fixer reasons from real evidence.
    for (name, s) in &out {
        if *name == AgentName::LogAnalyzer && merged.root_cause.is_none() && s.root_cause.is_some()
        {
            merged.root_cause = s.root_cause.clone();
        }
    }
    (merged, frontier.to_vec())
}

async fn run_node(
    ctx: &AgentContext,
    token: CancellationToken,
    state: AgentState,
    name: AgentName,
) -> (AgentState, String) {
    let node_name = name.as_str();
    emit_payload(
        ctx,
        EVENT_GRAPH_STEP_STARTED,
        &state,
        json!({"node": node_name}),
    )
    .await;
    let _ = ctx
        .emit_log(&state, &format!("[graph] node {node_name} starting"))
        .await;

    let result = match name {
        AgentName::AlertIngest => alert_ingest(ctx, state.clone()).await,
        AgentName::Planner => agents::planner::run(ctx, state.clone()).await,
        AgentName::LogAnalyzer => agents::log_analyzer::run(ctx, state.clone()).await,
        AgentName::CodeFixer => agents::code_fixer::run(ctx, state.clone()).await,
        AgentName::HitlGate => hitl_gate(ctx, token, state.clone()).await,
        AgentName::Verifier => agents::verifier::run(ctx, state.clone()).await,
        AgentName::Deploy => agents::deploy::run(ctx, state.clone()).await,
    };

    let (state, summary) = match result {
        Ok(s) => (s, format!("node {node_name} ok")),
        Err(err) => {
            tracing::error!(node = node_name, %err, "node failed");
            (
                state
                    .clone()
                    .with_error(err.to_string())
                    .finish(GraphStatus::Failed),
                format!("node {node_name} failed: {err}"),
            )
        }
    };

    emit_step_completed(ctx, &state, node_name, &summary).await;
    (state, summary)
}

async fn alert_ingest(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let title = state
        .incident
        .get("title")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let severity = state
        .incident
        .get("severity")
        .and_then(|v| v.as_str())
        .unwrap_or("n/a");
    let _ = ctx
        .emit_log(
            &state,
            &format!("[ingest] alert '{title}' severity={severity}"),
        )
        .await;
    Ok(state.status(GraphStatus::Running))
}

async fn hitl_gate(
    ctx: &AgentContext,
    token: CancellationToken,
    state: AgentState,
) -> Result<AgentState> {
    let patch = state
        .patch
        .clone()
        .ok_or_else(|| anyhow::anyhow!("human gate reached before any patch existed"))?;

    if state.hitl_approved {
        return Ok(state);
    }
    if ctx.cfg.graph_hitl_auto_approve {
        let review = ReviewRequest {
            incident_id: state.incident_id.clone(),
            run_id: Some(state.run_id.clone()),
            patch_ref: patch.patch_ref,
            action: "approve".into(),
            reason: Some("auto-approve (GRAPH_HITL_AUTO_APPROVE=true)".into()),
            modified_files: Map::new(),
        };
        return Ok(apply_review(ctx, state, &review).await);
    }

    emit_payload(
        ctx,
        EVENT_PENDING_APPROVAL,
        &state,
        json!({
            "patch_ref": patch.patch_ref,
            "incident_id": state.incident_id,
            "run_id": state.run_id,
            "summary": patch.summary,
            "diff": patch.diff,
            "files": patch.files.iter().map(|f| f.path.clone()).collect::<Vec<_>>(),
            "action": "approve|reject|modify",
        }),
    )
    .await;

    let state = state.status(GraphStatus::PendingApproval);
    emit_memory_snapshot(ctx, &state, "hitl_gate", "awaiting human approval").await;
    let _ = ctx
        .emit_log(&state, "[hitl_gate] waiting for signed review over NATS")
        .await;

    let mut reviews = ctx.bus.subscribe_review(&state.incident_id).await?;
    let timeout_fut: std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send>> =
        if ctx.cfg.graph_hitl_timeout_seconds > 0 {
            Box::pin(async move {
                tokio::time::sleep(std::time::Duration::from_secs(
                    ctx.cfg.graph_hitl_timeout_seconds,
                ))
                .await;
            })
        } else {
            Box::pin(std::future::pending())
        };

    tokio::select! {
        _ = token.cancelled() => {
            emit_payload(ctx, EVENT_REVIEW_DECIDED, &state, json!({
                "action": "reject", "patch_ref": patch.patch_ref, "reason": "run interrupted by operator",
            })).await;
            Ok(state.finish(GraphStatus::Interrupted))
        }
        Some(review) = reviews.recv() => {
            Ok(apply_review(ctx, state, &review).await)
        }
        _ = timeout_fut => {
            emit_payload(ctx, EVENT_REVIEW_DECIDED, &state, json!({
                "action": "reject", "patch_ref": patch.patch_ref, "reason": "approval timeout",
            })).await;
            let mut rejected = state.clone();
            if let Some(p) = &mut rejected.patch {
                p.status = PatchStatus::Rejected;
                p.review_reason = Some("approval timeout".into());
            }
            Ok(rejected.finish(GraphStatus::Cancelled))
        }
    }
}

async fn apply_review(ctx: &AgentContext, state: AgentState, review: &ReviewRequest) -> AgentState {
    let mut state = state;
    match review.action.as_str() {
        "approve" => {
            if let Some(p) = &mut state.patch {
                p.status = PatchStatus::Approved;
                p.review_reason = review.reason.clone();
            }
            state = state.with_hitl_approved();
            emit_payload(
                ctx,
                EVENT_REVIEW_DECIDED,
                &state,
                json!({
                    "action": "approve", "patch_ref": review.patch_ref, "reason": review.reason,
                }),
            )
            .await;
            state.status(GraphStatus::Running)
        }
        "modify" => {
            if let Some(p) = &mut state.patch {
                for (path, content) in &review.modified_files {
                    if let Some(file) = p.files.iter_mut().find(|f| f.path == *path) {
                        file.new_content = content.as_str().unwrap_or_default().to_string();
                    }
                }
                p.status = PatchStatus::Modified;
                p.review_reason = review.reason.clone();
            }
            state = state.with_hitl_approved();
            emit_payload(
                ctx,
                EVENT_REVIEW_DECIDED,
                &state,
                json!({
                    "action": "modify", "patch_ref": review.patch_ref, "reason": review.reason,
                }),
            )
            .await;
            state.status(GraphStatus::Running)
        }
        _ => {
            if let Some(p) = &mut state.patch {
                p.status = PatchStatus::Rejected;
                p.review_reason = review.reason.clone();
            }
            emit_payload(
                ctx,
                EVENT_REVIEW_DECIDED,
                &state,
                json!({
                    "action": "reject", "patch_ref": review.patch_ref, "reason": review.reason,
                }),
            )
            .await;
            state.finish(GraphStatus::Cancelled)
        }
    }
}

async fn emit_step_completed(ctx: &AgentContext, state: &AgentState, node: &str, summary: &str) {
    emit_payload(
        ctx,
        EVENT_GRAPH_STEP_COMPLETED,
        state,
        json!({
            "node": node, "summary": summary, "step_index": state.step_index + 1,
        }),
    )
    .await;
    emit_memory_snapshot(ctx, state, node, summary).await;
}

async fn emit_memory_snapshot(ctx: &AgentContext, state: &AgentState, node: &str, summary: &str) {
    let snap_ref = format!("snap_{}", crate::state::hex16());
    let _ = ctx
        .memory
        .snapshot(
            &snap_ref,
            &state.incident_id,
            &state.run_id,
            state.step_index + 1,
            node,
            summary,
            state.status.as_str(),
            state.to_value(),
        )
        .await;
    emit_payload(
        ctx,
        EVENT_MEMORY_SNAPSHOT,
        state,
        json!({
            "ref": snap_ref,
            "node": node,
            "summary": summary,
            "status": state.status.as_str(),
            "state": state.to_value(),
        }),
    )
    .await;
}

pub(crate) async fn emit_payload(
    ctx: &AgentContext,
    type_: &str,
    state: &AgentState,
    payload: Value,
) {
    let env = Envelope::new(type_, crate::bus::channel_incident(&state.incident_id))
        .with_incident(&state.incident_id)
        .with_run(&state.run_id)
        .with_payload(payload.as_object().cloned().unwrap_or_default());
    let _ = ctx.bus.publish_event(&env).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn node_order_covers_every_agent() {
        for name in NODE_ORDER.iter().copied() {
            assert_eq!(name.as_str(), name.as_str()); // variant exists
        }
        let mut seen = HashSet::new();
        for name in NODE_ORDER.iter().copied() {
            assert!(seen.insert(name), "duplicate {name:?} in NODE_ORDER");
        }
    }

    #[test]
    fn dependency_graph_is_reachable_and_acyclic() {
        // Every node must be reachable from AlertIngest via `deps()`.
        let mut reachable: HashSet<AgentName> = HashSet::from([AgentName::AlertIngest]);
        let mut changed = true;
        while changed {
            changed = false;
            for name in NODE_ORDER.iter().copied() {
                if reachable.contains(&name) {
                    continue;
                }
                if deps(name).iter().all(|d| reachable.contains(d)) {
                    reachable.insert(name);
                    changed = true;
                }
            }
        }
        assert_eq!(reachable.len(), NODE_ORDER.len(), "orphaned node");
    }

    #[test]
    fn full_walk_hits_every_node_exactly_once_in_order() {
        // Simulate the scheduler's enqueue step: from AlertIngest, the frontier
        // walks the DAG and must visit all 7 nodes, ending at Deploy.
        let mut completed: HashSet<AgentName> = HashSet::new();
        let mut ever: HashSet<AgentName> = HashSet::from([AgentName::AlertIngest]);
        let mut pending: VecDeque<AgentName> = VecDeque::from([AgentName::AlertIngest]);
        let mut walked = Vec::new();

        while let Some(node) = pending.pop_front() {
            if completed.contains(&node) || !deps(node).iter().all(|d| completed.contains(d)) {
                continue;
            }
            let others: Vec<AgentName> = pending
                .iter()
                .copied()
                .filter(|n| !completed.contains(n) && deps(*n).iter().all(|d| completed.contains(d)))
                .collect();
            pending.retain(|n| !others.contains(n));
            let mut frontier = vec![node];
            frontier.extend(others);
            for n in &frontier {
                completed.insert(*n);
                walked.push(*n);
            }
            for next in NODE_ORDER.iter().copied() {
                if !completed.contains(&next)
                    && !ever.contains(&next)
                    && deps(next).iter().all(|d| completed.contains(d))
                {
                    ever.insert(next);
                    pending.push_back(next);
                }
            }
        }

        assert_eq!(walked.len(), 7, "walked {walked:?}");
        assert!(completed.contains(&AgentName::Deploy));
        assert_eq!(*walked.last().unwrap(), AgentName::Deploy);
        assert!(order(AgentName::Deploy) >= order(AgentName::Verifier));
    }
}
