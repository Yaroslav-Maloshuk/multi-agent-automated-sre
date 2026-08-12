//! Code Fixer agent: produces a real, diff-able source patch inside an
//! isolated sandbox against the incident's file snapshot. Streams live typing
//! telemetry and a unified Git-style diff; never touches the host filesystem.

use anyhow::{Context, Result};
use serde_json::{json, Map, Value};

use crate::context::{
    AgentContext, EVENT_PATCH_DIFF, EVENT_PATCH_GENERATED, EVENT_PATCH_TYPING,
    EVENT_SANDBOX_ACQUIRED, EVENT_SANDBOX_RELEASED,
};
use crate::state::{AgentState, Patch, PatchFile, PatchStatus};

const SYSTEM_PROMPT: &str = r#"You are a staff engineer fixing a production incident. You are given a source file and a root-cause hypothesis. Rewrite the file to eliminate the root cause WITHOUT changing its public interface. Preserve all existing tests' expectations. If verification feedback is provided, address it precisely. Respond with the COMPLETE new file contents only, no commentary, no markdown fences."#;

pub async fn run(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let files = match state.incident.get("files").and_then(|v| v.as_object()) {
        Some(files) if !files.is_empty() => files.clone(),
        _ => {
            return Err(anyhow::anyhow!("code_fixer requires incident.files"));
        }
    };
    let target_file = state
        .incident
        .get("target_file")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| first_file(&files));

    let old_content = files
        .get(&target_file)
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if old_content.trim().is_empty() {
        return Err(anyhow::anyhow!("code_fixer: target file is empty"));
    }

    let root_cause = state
        .root_cause
        .as_ref()
        .map(|r| r.hypothesis.clone())
        .unwrap_or_else(|| "root cause under investigation".to_string());
    let feedback = state
        .verifier_feedback
        .clone()
        .unwrap_or_else(|| "no previous attempt".to_string());

    let mut pool = ctx.pool.lock().await;
    let handle = pool.acquire().await.context("acquire sandbox")?;
    drop(pool);

    let state = {
        let mut m = Map::new();
        m.insert("container_id".into(), json!(handle.name()));
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_SANDBOX_ACQUIRED).with_payload(m))
            .await;
        state
    };

    let user_prompt = format!(
        "Root-cause hypothesis: {root_cause}\nVerification feedback: {feedback}\n\nFile: {target_file}\n\n```\n{old_content}\n```\n\nRespond with the COMPLETE rewritten file."
    );

    let (new_content, state) = match ctx.complete(&state, SYSTEM_PROMPT, &user_prompt).await {
        Ok((text, next)) => {
            let cleaned = strip_fences(&text);
            let _ = ctx
                .emit_log(
                    &next,
                    &format!(
                        "[code_fixer] llm produced {} lines",
                        cleaned.lines().count()
                    ),
                )
                .await;
            (cleaned, next)
        }
        Err(err) => {
            let _ = ctx
                .emit_log(
                    &state,
                    &format!("[code_fixer] llm unavailable, applying rule-based fix ({err})"),
                )
                .await;
            (rule_fix(&target_file, &old_content, &root_cause), state)
        }
    };

    let state = stream_typing(ctx, &state, &new_content).await;

    if let Err(err) = handle.write_file(&target_file, &new_content).await {
        let mut pool = ctx.pool.lock().await;
        pool.release(handle).await;
        drop(pool);
        return Err(err.context("write patched file into sandbox"));
    }

    let diff = unified_diff(&target_file, &old_content, &new_content);
    let patch = Patch {
        patch_ref: format!("patch_{}", crate::state::hex16()),
        files: vec![PatchFile {
            path: target_file.clone(),
            old_content,
            new_content: new_content.clone(),
        }],
        diff: diff.clone(),
        summary: format!("Fix for {root_cause}"),
        status: PatchStatus::Draft,
        review_reason: None,
    };

    {
        let mut m = Map::new();
        m.insert("patch_ref".into(), json!(patch.patch_ref));
        m.insert("diff".into(), json!(diff.clone()));
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_PATCH_DIFF).with_payload(m))
            .await;
    }
    {
        let mut m = Map::new();
        m.insert("patch_ref".into(), json!(patch.patch_ref));
        m.insert("summary".into(), json!(patch.summary));
        m.insert("files".into(), json!([target_file]));
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_PATCH_GENERATED).with_payload(m))
            .await;
    }

    let mut pool = ctx.pool.lock().await;
    pool.release(handle).await;
    drop(pool);
    let _ = ctx
        .emit(
            &ctx.base(&state, EVENT_SANDBOX_RELEASED)
                .with_payload(Map::new()),
        )
        .await;

    let state = state.replace_patch(patch);
    let _ = ctx
        .emit_log(
            &state,
            &format!(
                "[code_fixer] patch {} ready ({} file(s))",
                state
                    .patch
                    .as_ref()
                    .map(|p| p.patch_ref.clone())
                    .unwrap_or_default(),
                1
            ),
        )
        .await;
    Ok(state)
}

async fn stream_typing(ctx: &AgentContext, state: &AgentState, content: &str) -> AgentState {
    let state = state.clone();
    let chunks: Vec<&str> = content
        .as_bytes()
        .chunks(64)
        .map(|c| std::str::from_utf8(c).unwrap_or(""))
        .collect();
    let mut acc = String::new();
    for chunk in chunks.into_iter().take(96) {
        acc.push_str(chunk);
        let mut m = Map::new();
        m.insert("typed".into(), json!(chunk));
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_PATCH_TYPING).with_payload(m))
            .await;
        tokio::time::sleep(std::time::Duration::from_millis(3)).await;
    }
    state
}

fn strip_fences(text: &str) -> String {
    let t = text.trim();
    let body = if let Some(stripped) = t.strip_prefix("```") {
        let after_first_line = stripped
            .split_once('\n')
            .map(|(_, rest)| rest)
            .unwrap_or(stripped);
        after_first_line
            .trim_end()
            .trim_end_matches("```")
            .trim_end()
    } else {
        t
    };
    body.to_string()
}

fn unified_diff(path: &str, old: &str, new: &str) -> String {
    let diff = similar::TextDiff::from_lines(old, new);
    diff.unified_diff()
        .context_radius(3)
        .header(&format!("a/{path}"), &format!("b/{path}"))
        .to_string()
}

fn first_file(files: &Map<String, Value>) -> String {
    files
        .keys()
        .next()
        .cloned()
        .unwrap_or_else(|| "app.py".to_string())
}

pub fn rule_fix(target_file: &str, old_content: &str, root_cause: &str) -> String {
    let mut out = old_content.to_string();
    if old_content.contains("requests.get") {
        out = out.replace("requests.get", "requests.get(timeout=15)");
    }
    if old_content.contains("requests.post") {
        out = out.replace("requests.post", "requests.post(timeout=15)");
    }
    if old_content.contains("redis.Redis")
        && !old_content.contains("socket_timeout")
        && !old_content.contains("socket_connect_timeout")
    {
        out = out.replace("redis.Redis", "redis.Redis(socket_timeout=5)");
    }
    if old_content.contains("sqlite3.connect") && !old_content.contains("timeout=") {
        out = out.replace("sqlite3.connect", "sqlite3.connect(timeout=10)");
    }
    if old_content.contains("time.sleep") {
        out = out.replace("time.sleep", "time.sleep(0.1)");
    }
    if !old_content.contains("// sre-fix") && !old_content.contains("# sre-fix") {
        let annotation = if target_file.ends_with(".py") {
            "# sre-fix: "
        } else {
            "// sre-fix: "
        };
        out.push_str(&format!("\n{annotation}{root_cause}"));
    }
    out
}
