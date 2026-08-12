// Contract test — asserts the gateway API shape the workers and command
// center depend on. Runs as a short single-VU smoke.
//
//   docker compose up -d nats valkey qdrant postgres gateway
//   k6 run tests/k6/contract.js
import http from 'k6/http';
import { check } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';

const headers = { 'Content-Type': 'application/json' };

export default function () {
  // 1. health exposes version
  const health = http.get(`${BASE}/api/v1/health`);
  check(health, {
    'health 200': (r) => r.status === 200,
    'health version': (r) => r.json().version !== undefined,
  });

  // 2. create → get → list lifecycle
  const created = http.post(
    `${BASE}/api/v1/incidents`,
    JSON.stringify({ title: 'contract smoke', severity: 'P1', tags: ['contract'] }),
    { headers },
  );
  check(created, {
    'create 200/201': (r) => r.status === 200 || r.status === 201,
    'create returns incident_id': (r) => typeof r.json().incident_id === 'string',
  });
  const id = created.json().incident_id;
  check(id, { 'incident_id present': (v) => typeof v === 'string' && v.length > 0 });

  const got = http.get(`${BASE}/api/v1/incidents/${id}`);
  check(got, {
    'get 200': (r) => r.status === 200,
    'get echoes id': (r) => r.json().incident_id === id,
  });

  const list = http.get(`${BASE}/api/v1/incidents`);
  check(list, {
    'list 200': (r) => r.status === 200,
    'list contains incident': (r) => {
      const items = r.json().incidents || r.json().items || r.json();
      return JSON.stringify(items).includes(id);
    },
  });

  // 3. run endpoint accepts dispatch (worker may be offline)
  const run = http.post(`${BASE}/api/v1/incidents/${id}/run`);
  check(run, {
    'run accepted': (r) => r.status === 200 || r.status === 202,
  });
}
