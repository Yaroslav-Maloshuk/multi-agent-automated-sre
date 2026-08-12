#!/usr/bin/env bash
# -------------------------------------------------------------------------
# Chaos / soak: verifies the fabric recovers from component loss.
#
#   make chaos            # or: bash tests/chaos/soak.sh
#
# Requires: docker compose infra up (nats/valkey/qdrant/postgres/gateway)
# and the worker running. It repeatedly:
#   1. creates an incident + starts a run
#   2. kills the worker mid-flight
#   3. restarts the worker
#   4. waits for the run to reach a terminal state via the gateway REST API
# -------------------------------------------------------------------------
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"
WORKER_SVC="${WORKER_SVC:-worker}"
CYCLES="${CYCLES:-3}"
DOWNTIME_SECS="${DOWNTIME_SECS:-8}"

curl -fsS "$BASE_URL/api/v1/health" >/dev/null || {
  echo "gateway not reachable at $BASE_URL — run 'make infra && make up' first" >&2
  exit 1
}

terminal_status() {
  local id="$1"
  curl -fsS "$BASE_URL/api/v1/incidents/$id" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true
}

pass=0
for i in $(seq 1 "$CYCLES"); do
  echo "-- cycle $i/$CYCLES"
  incident=$(curl -fsS -X POST "$BASE_URL/api/v1/incidents" \
    -H 'Content-Type: application/json' \
    -d "{\"title\":\"chaos-soak $i\",\"severity\":\"P2\"}")
  id=$(printf '%s' "$incident" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("incident_id",""))')
  if [ -z "$id" ]; then
    echo "!! no incident_id returned; aborting cycle" >&2
    continue
  fi
  curl -fsS -X POST "$BASE_URL/api/v1/incidents/$id/run" >/dev/null || true

  echo "  killing worker for ${DOWNTIME_SECS}s (incident $id)"
  docker compose stop "$WORKER_SVC" >/dev/null
  sleep "$DOWNTIME_SECS"
  docker compose start "$WORKER_SVC" >/dev/null
  echo "  worker restarted; waiting for terminal state"

  ok=0
  for _ in $(seq 1 40); do
    status=$(terminal_status "$id")
    case "$status" in
      COMPLETED|FAILED|CANCELLED|INTERRUPTED)
        echo "  -> $status after recovery"
        ok=1
        break
        ;;
    esac
    sleep 3
  done
  if [ "$ok" = 1 ]; then
    pass=$((pass + 1))
  else
    echo "  !! incident $id never reached a terminal state" >&2
  fi
done

echo "chaos/soak result: $pass/$CYCLES cycles recovered"
[ "$pass" -eq "$CYCLES" ]
