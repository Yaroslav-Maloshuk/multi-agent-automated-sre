const API_BASE =
  process.env['API_BASE'] ??
  process.env['EXPO_PUBLIC_API_BASE'] ??
  'http://localhost:8000';

export interface IncidentSummary {
  incident_id: string;
  status: string;
  severity: string;
}

export async function fetchIncidents(): Promise<IncidentSummary[]> {
  const res = await fetch(`${API_BASE}/api/v1/incidents`);
  if (!res.ok) {
    throw new Error(`GET /api/v1/incidents failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchHealth(): Promise<unknown> {
  const res = await fetch(`${API_BASE}/api/v1/health`);
  if (!res.ok) {
    throw new Error(`GET /api/v1/health failed: ${res.status}`);
  }
  return res.json();
}
