//! The five agents of the stateful multi-agent graph. Each agent is a stateless
//! actor: all dependencies arrive via `AgentContext` and the immutable
//! `AgentState` is threaded through and returned. Ported from
//! legacy `app/agents/` — llm round-trips degrade to deterministic fallbacks so
//! the engine never stalls on model availability.

pub mod code_fixer;
pub mod deploy;
pub mod log_analyzer;
pub mod planner;
pub mod verifier;
