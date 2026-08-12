//! Deploy agent: marks an approved, verified patch as applied and hands the
//! diff to the CD pipeline when `DEPLOY_WEBHOOK_URL` is configured.

use anyhow::{Context, Result};
use serde_json::{json, Map};

use crate::context::{AgentContext, EVENT_PATCH_APPLIED};
use crate::state::{AgentState, GraphStatus, Patch, PatchStatus};

pub async fn run(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let patch = state
        .patch
        .clone()
        .context("deploy requires an approved, verified patch")?;

    let applied = Patch {
        status: PatchStatus::Applied,
        review_reason: patch.review_reason.clone(),
        ..patch.clone()
    };
    let state = state.replace_patch(applied.clone());

    {
        let mut m = Map::new();
        m.insert("patch_ref".into(), json!(applied.patch_ref));
        m.insert("summary".into(), json!(applied.summary));
        m.insert(
            "files".into(),
            json!(applied
                .files
                .iter()
                .map(|f| f.path.clone())
                .collect::<Vec<_>>()),
        );
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_PATCH_APPLIED).with_payload(m))
            .await;
    }
    let _ = ctx
        .emit_log(
            &state,
            &format!("[deploy] patch {} applied", applied.patch_ref),
        )
        .await;

    if let Some(webhook) = &ctx.cfg.deploy_webhook_url {
        let client = reqwest::Client::new();
        match client
            .post(webhook)
            .json(&json!({
                "incident_id": state.incident_id,
                "run_id": state.run_id,
                "patch_ref": applied.patch_ref,
                "summary": applied.summary,
                "diff": applied.diff,
            }))
            .timeout(std::time::Duration::from_secs(10))
            .send()
            .await
        {
            Ok(resp) => {
                let _ = ctx
                    .emit_log(
                        &state,
                        &format!("[deploy] webhook delivered (status {})", resp.status()),
                    )
                    .await;
            }
            Err(err) => {
                let _ = ctx
                    .emit_log(&state, &format!("[deploy] webhook delivery failed: {err}"))
                    .await;
            }
        }
    }

    Ok(state.finish(GraphStatus::Completed))
}
