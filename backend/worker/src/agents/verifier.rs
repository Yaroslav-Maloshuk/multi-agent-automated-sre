//! Verifier agent: runs pytest + Ruff inside the sandbox on the patch. Any
//! failure becomes structured feedback for the self-correction loop (the
//! executor re-queues the fixer with this feedback).

use anyhow::{Context, Result};
use serde_json::{json, Map};

use crate::context::{AgentContext, EVENT_SANDBOX_ACQUIRED, EVENT_SANDBOX_RELEASED};
use crate::sandbox::{linter_unavailable, no_tests_collected, parse_failures};
use crate::state::{AgentState, VerificationReport};

pub async fn run(ctx: &AgentContext, state: AgentState) -> Result<AgentState> {
    let patch = state
        .patch
        .clone()
        .context("verifier requires a generated patch")?;
    if patch.files.is_empty() {
        return Err(anyhow::anyhow!(
            "verifier requires at least one patched file"
        ));
    }

    let attempt = state.verifier_attempt + 1;
    let mut pool = ctx.pool.lock().await;
    let handle = pool.acquire().await.context("acquire sandbox")?;
    drop(pool);

    {
        let mut m = Map::new();
        m.insert("container_id".into(), json!(handle.name()));
        let _ = ctx
            .emit(&ctx.base(&state, EVENT_SANDBOX_ACQUIRED).with_payload(m))
            .await;
    }

    for patch_file in &patch.files {
        handle
            .write_file(&patch_file.path, &patch_file.new_content)
            .await
            .context("write patch into sandbox")?;
    }

    let _ = ctx
        .emit_log(
            &state,
            &format!("[verifier] running pytest (attempt {attempt})"),
        )
        .await;
    let start = std::time::Instant::now();
    let test_result = handle.run("pytest -q --tb=short", 240).await;
    let duration = start.elapsed().as_secs_f64();

    let changed: Vec<String> = patch.files.iter().map(|f| f.path.clone()).collect();
    let mut lint_output = String::new();
    let mut lint_ok = true;
    if !changed.is_empty() {
        match handle
            .run(&format!("ruff check {}", changed.join(" ")), 60)
            .await
        {
            Ok(lint) => {
                lint_output = format!("{}{}", lint.stdout, lint.stderr);
                lint_ok = lint.ok || linter_unavailable(lint.exit_code, &lint_output);
            }
            Err(err) => {
                lint_output = format!("[lint exec error] {err}");
            }
        }
    }

    let tests_passed = match &test_result {
        Ok(r) => r.ok || no_tests_collected(&format!("{}{}", r.stdout, r.stderr)),
        Err(_) => false,
    };
    let raw_output = match &test_result {
        Ok(r) => format!("{}{}", r.stdout, r.stderr),
        Err(err) => format!("[pytest exec error] {err}"),
    };
    let failures = parse_failures(&raw_output);
    let ok = tests_passed && lint_ok;
    let feedback = if ok {
        String::new()
    } else {
        let mut fb = Vec::new();
        if !failures.is_empty() {
            fb.push(format!("test failures:\n{}", failures.join("\n")));
        }
        if !lint_ok {
            fb.push(format!(
                "lint failures:\n{}",
                lint_output.chars().take(500).collect::<String>()
            ));
        }
        fb.join("\n")
    };

    let report = VerificationReport {
        ok,
        output: raw_output.clone(),
        failures,
        duration_seconds: duration,
        attempt,
        coverage: None,
    };
    let _ = ctx
        .emit_log(
            &state,
            &format!(
                "[verifier] attempt {attempt} {} in {:.1}s",
                if ok { "PASSED" } else { "FAILED" },
                duration
            ),
        )
        .await;

    let mut pool = ctx.pool.lock().await;
    pool.release(handle).await;
    drop(pool);
    let _ = ctx
        .emit(
            &ctx.base(&state, EVENT_SANDBOX_RELEASED)
                .with_payload(Map::new()),
        )
        .await;

    let state = state.replace_verification(report, attempt, if ok { None } else { Some(feedback) });
    let _ = ctx
        .emit_step(
            &state,
            "verification",
            json!({"ok": ok, "attempt": attempt, "duration_seconds": duration}),
        )
        .await;
    Ok(state)
}
