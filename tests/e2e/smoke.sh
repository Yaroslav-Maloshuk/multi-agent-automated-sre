#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# E2E smoke test — proves the full control plane responds end to end:
#   Go gateway (REST + SSE + Templ dashboard) → AI gateway (chat,
#   embeddings, RAG documents/search, structured, DSPy, analytics, evals).
#
# Usage:   bash tests/e2e/smoke.sh [AI_PORT]
# Pre:     `make ai` running (or docker compose up --build).
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
AI_URL="${AI_URL:-http://localhost:8002}"
PASS=0
FAIL=0

check() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == *"$expected"* ]]; then
    echo "  ok  ${name}"
    PASS=$((PASS + 1))
  else
    echo "FAIL  ${name}: expected \"${expected}\" got \"${actual}\""
    FAIL=$((FAIL + 1))
  fi
}

json() { curl -s -m 20 -H "Content-Type: application/json" "$@"; }

echo "→ Go gateway (${GATEWAY_URL})"
if curl -sf -m 5 "${GATEWAY_URL}/api/v1/health" >/dev/null 2>&1; then
  check "gateway health" '"status"' "$(curl -s -m 5 "${GATEWAY_URL}/api/v1/health")"
  check "dashboard renders" 'htmx' "$(curl -s -m 5 "${GATEWAY_URL}/")"
  check "SSE events stream" 'data:' "$(curl -s -m 5 -N "${GATEWAY_URL}/api/v1/events/stream")"
else
  echo "  skip gateway checks (not running on ${GATEWAY_URL})"
fi

echo "→ AI gateway (${AI_URL})"
check "ai health" '"status"' "$(curl -s -m 5 "${AI_URL}/health")"
check "chat completion" '"choices"' \
  "$(json "${AI_URL}/v1/chat/completions" -d '{"model":"local/test","messages":[{"role":"user","content":"planner: plan the incident"}]}')"
check "chat SSE stream" 'data:' \
  "$(json "${AI_URL}/v1/chat/completions" -d '{"model":"local/test","stream":true,"messages":[{"role":"user","content":"planner: plan"}]}')"
check "embeddings" '"embedding"' \
  "$(json "${AI_URL}/v1/embeddings" -d '{"input":"no space left on device"}')"
check "structured" '"root_cause"' \
  "$(json "${AI_URL}/v1/structured" -d '{"prompt":"log_analyzer: diagnose","schema":{"properties":{"root_cause":{"type":"string"}}}}')"
check "dspy plan" '"owner"' \
  "$(json "${AI_URL}/v1/dspy/plan" -d '{"context":"disk full on node-1"}')"
check "rag documents" '"indexed_stores"' \
  "$(json "${AI_URL}/v1/rag/documents" -d '{"id":"smoke_doc","text":"RESOLVED INCIDENT: disk full on node-1; root cause: log rotation; fix: cleanup","metadata":{"kind":"postmortem"}}')"
check "rag search" '"hits"' \
  "$(json "${AI_URL}/v1/rag/search" -d '{"query":"no space left on device","top_k":3}')"
check "analytics" '"retrieval_recall"' "$(curl -s -m 5 "${AI_URL}/v1/analytics")"
check "eval" '"faithfulness"' \
  "$(json "${AI_URL}/v1/eval" -d '{"questions":["disk full?"],"answers":["clean /var/log"],"contexts":[["clean /var/log"]],"ground_truths":[["clean /var/log"]]}')"

echo
echo "E2E smoke: ${PASS} passed, ${FAIL} failed"
exit $((FAIL > 0 ? 1 : 0))
