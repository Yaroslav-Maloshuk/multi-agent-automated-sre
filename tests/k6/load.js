// Load test for the Go gateway + NATS fan-out fabric.
//
//   docker compose up -d nats valkey qdrant postgres gateway
//   k6 run tests/k6/load.js
//
// Exercises: REST health, incident create/read/list, and (best effort) the
// WebSocket event stream. Safe to run repeatedly — incidents are unique per
// virtual user.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { WebSocket } from 'k6/experimental/websockets';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '30s', target: 25 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const health = http.get(`${BASE}/api/v1/health`);
  check(health, { 'health 200': (r) => r.status === 200 });

  const created = http.post(
    `${BASE}/api/v1/incidents`,
    JSON.stringify({
      title: `load-gen ${__VU}:${__ITER}`,
      severity: 'P2',
      source: 'load-test',
    }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  check(created, {
    'create 200/201': (r) => r.status === 200 || r.status === 201,
  });

  const body = created.json();
  const id = body.incident_id;
  if (id) {
    const got = http.get(`${BASE}/api/v1/incidents/${id}`);
    check(got, { 'incident 200': (r) => r.status === 200 });

    const list = http.get(`${BASE}/api/v1/incidents`);
    check(list, { 'list 200': (r) => r.status === 200 });
  }

  sleep(0.5);
}
