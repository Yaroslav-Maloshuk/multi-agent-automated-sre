import type {
  AgentInfo,
  EventEnvelope,
  ForkInput,
  Health,
  IncidentCreateInput,
  IncidentDetail,
  IncidentSummary,
  RoiData,
  SandboxStats,
  SnapshotBlob,
  SnapshotMeta,
} from "./types";

const API_BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body: unknown = await res.json();
      detail = extractDetail(body) ?? detail;
    } catch {
      // non-JSON error body; keep status text
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

function extractDetail(body: unknown): string | null {
  if (body && typeof body === "object") {
    const d = (body as Record<string, unknown>).detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") {
      const msg = (d as Record<string, unknown>).message;
      if (typeof msg === "string") return msg;
    }
    const msg = (body as Record<string, unknown>).message;
    if (typeof msg === "string") return msg;
  }
  return null;
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function listIncidents(): Promise<{ incidents: IncidentSummary[] }> {
  return request<{ incidents: IncidentSummary[] }>("/incidents");
}

export function getIncident(incidentId: string): Promise<IncidentDetail> {
  return request<IncidentDetail>(`/incidents/${encodeURIComponent(incidentId)}`);
}

export function createIncident(input: IncidentCreateInput): Promise<{ incident_id: string; incident: Record<string, unknown> }> {
  return request<{ incident_id: string; incident: Record<string, unknown> }>("/incidents", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function runIncident(incidentId: string): Promise<{ incident_id: string; status: string }> {
  return request<{ incident_id: string; status: string }>(`/incidents/${encodeURIComponent(incidentId)}/run`, {
    method: "POST",
  });
}

export function reviewIncident(
  incidentId: string,
  body: { action: "approve" | "reject"; reason?: string; patch_ref?: string },
): Promise<{ incident_id: string; accepted: boolean; action: string }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/review`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function interruptIncident(incidentId: string): Promise<{ incident_id: string; status: string }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/interrupt`, { method: "POST" });
}

export function resumeIncident(incidentId: string): Promise<{ incident_id: string; status: string }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/resume`, { method: "POST" });
}

export function listSnapshots(incidentId: string): Promise<{ incident_id: string; snapshots: SnapshotMeta[] }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/snapshots`);
}

export function getSnapshot(incidentId: string, ref: string): Promise<{ incident_id: string; ref: string; snapshot: SnapshotBlob }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/snapshots/${encodeURIComponent(ref)}`);
}

export function forkIncident(
  incidentId: string,
  body: ForkInput,
): Promise<{ incident_id: string; incident: Record<string, unknown> }> {
  return request(`/incidents/${encodeURIComponent(incidentId)}/fork`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listAgents(): Promise<{ agents: AgentInfo[] }> {
  return request<{ agents: AgentInfo[] }>("/agents");
}

export function getSandboxes(): Promise<SandboxStats> {
  return request<SandboxStats>("/sandboxes");
}

export function warmSandboxes(count?: number): Promise<{ created: number; warm: number; in_use: number }> {
  return request(`/sandboxes/warm`, {
    method: "POST",
    body: JSON.stringify(count ? { count } : {}),
  });
}

export function getRoi(): Promise<RoiData> {
  return request<RoiData>("/metrics/roi");
}

export function wsUrl(channels: string[]): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const query = channels.length ? `?channels=${encodeURIComponent(channels.join(","))}` : "";
  return `${proto}//${window.location.host}/ws/stream${query}`;
}

export function parseEnvelope(raw: string): EventEnvelope | null {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && typeof (parsed as EventEnvelope).type === "string") {
      return parsed as EventEnvelope;
    }
    return null;
  } catch {
    return null;
  }
}

export function envelopeMessage(env: EventEnvelope): string {
  const p = env.payload;
  for (const key of ["message", "log", "summary", "error", "hypothesis", "title", "label"]) {
    const v = p[key];
    if (typeof v === "string" && v.length > 0) return v;
  }
  if (typeof p.status === "string") return `status → ${p.status}`;
  if (typeof p.node === "string") return `node: ${p.node}`;
  if (typeof p.agent === "string") return `agent: ${p.agent}`;
  return env.type;
}
