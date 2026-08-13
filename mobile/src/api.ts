const API_BASE =
  process.env['API_BASE'] ??
  process.env['EXPO_PUBLIC_API_BASE'] ??
  'http://localhost:8000';

export interface IncidentSummary {
  incident_id: string;
  title?: unknown;
  severity: string;
  status: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    throw new Error(`${init?.method ?? 'GET'} ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchIncidents(): Promise<IncidentSummary[]> {
  const data = await request<{ incidents: IncidentSummary[] }>('/api/v1/incidents');
  return data.incidents;
}

export async function fetchHealth(): Promise<unknown> {
  return request<unknown>('/api/v1/health');
}

export async function fetchIncidentDetail(
  id: string,
): Promise<{ incident?: unknown; state?: unknown; roi?: unknown }> {
  return request(`/api/v1/incidents/${id}`);
}

export async function createIncident(title: string, summary: string): Promise<{ incident_id: string }> {
  return request('/api/v1/incidents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      summary,
      severity: 'high',
      source: 'mobile',
      auto_run: false,
    }),
  });
}

export async function startRun(id: string): Promise<unknown> {
  return request(`/api/v1/incidents/${id}/run`, { method: 'POST' });
}

export async function interruptRun(id: string): Promise<unknown> {
  return request(`/api/v1/incidents/${id}/interrupt`, { method: 'POST' });
}

export async function reviewIncident(id: string, action: 'approve' | 'reject' | 'modify'): Promise<unknown> {
  return request(`/api/v1/incidents/${id}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  });
}
