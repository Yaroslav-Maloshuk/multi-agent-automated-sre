//! The Rust/Tokio worker pool of the Multi-Agent Automated SRE engine.
//!
//! Consumes incidents from the NATS JetStream work-queue, executes the stateful
//! async agent graph, and publishes every step to `sre.events.*` for the Go
//! gateway to fan out to the command center over WebSockets.
//!
//! The crate is a deliberately-complete implementation of the gateway contract:
//! a number of helpers (envelope builders, memory/sandbox stats, ROI utilities)
//! are kept as API surface for feature parity and CLI/debug use even when the
//! production run loop does not touch them, so dead-code is allowed wholesale.
#![allow(dead_code)]

mod agents;
mod bus;
mod config;
mod context;
mod graph;
mod llm;
mod memory;
mod sandbox;
mod state;

use std::sync::Arc;

use futures::StreamExt;
use tokio_util::sync::CancellationToken;

use config::WorkerConfig;
use state::AgentState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "auto_sre_worker=info,tokio_util=warn".into()),
        )
        .init();

    let cfg = WorkerConfig::load();
    tracing::info!(
        "auto-sre worker pool starting — nats={} provider={}",
        cfg.nats_url,
        cfg.llm_provider
    );

    let bus = bus::Bus::connect(&cfg.nats_url).await?;

    let llm: Arc<dyn llm::Llm> = Arc::from(llm::build_router(&cfg));
    let embed = llm::build_embedding_fn(&cfg);
    // With LLM_PROVIDER=ai the worker reasons over the AI gateway's hybrid RAG
    // (Qdrant + LanceDB + vectorless PageIndex, RRF); otherwise Qdrant memory.
    let memory: Arc<dyn memory::Memory> = if cfg.llm_provider == "ai" {
        Arc::new(memory::GatewayMemory::connect(&cfg, embed).await?)
    } else {
        Arc::new(memory::QdrantMemory::connect(&cfg, embed).await?)
    };

    let mut pool = sandbox::SandboxPool::connect(&cfg).await?;
    pool.warm().await?;
    let pool = Arc::new(tokio::sync::Mutex::new(pool));

    let ctx = context::AgentContext {
        bus: bus.clone(),
        llm: llm.clone(),
        memory: memory.clone(),
        pool,
        cfg: cfg.clone(),
    };

    spawn_sandbox_control(&bus, &ctx).await?;

    let mut runs = bus.subscribe_runs().await?;
    tracing::info!("worker pool ready — consuming from {}", cfg.run_request);

    while let Some(run) = runs.recv().await {
        let ctx = ctx.clone();
        let bus = bus.clone();
        tokio::spawn(async move {
            let token = CancellationToken::new();
            let _ = bus.subscribe_control(&run.incident_id, token.clone()).await;

            let incident = run.incident.clone();
            let state = AgentState::new(incident, Some(run.incident_id.clone()));
            tracing::info!(incident = %run.incident_id, run = %state.run_id, "executing graph");

            let outcome = graph::execute(ctx, token, state).await;
            tracing::info!(incident = %run.incident_id, status = ?outcome.state.status, tokens = ?outcome.state.token_usage, "run finished");
        });
    }

    Ok(())
}

/// Global control subscription for the shared sandbox pool (warm signal from
/// the gateway). Run-level interrupts are handled per-incident by the executor.
async fn spawn_sandbox_control(bus: &bus::Bus, ctx: &context::AgentContext) -> anyhow::Result<()> {
    let mut sub = bus.subscribe_raw(&bus.control_subject_wildcard()).await?;
    let pool = ctx.pool.clone();
    tokio::spawn(async move {
        while let Some(msg) = sub.next().await {
            match serde_json::from_slice::<bus::ControlMessage>(&msg.payload) {
                Ok(ctrl) if ctrl.action == "warm" => {
                    let count = ctrl.run_id.parse::<i64>().unwrap_or(0);
                    let mut pool = pool.lock().await;
                    let _ = pool.warm_count(count).await;
                }
                _ => {}
            }
        }
    });
    Ok(())
}
