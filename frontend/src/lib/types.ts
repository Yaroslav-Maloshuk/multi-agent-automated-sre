export interface Health {
  status: string;
  version: string;
  environment: string;
  dependencies: Record<string, string>;
}

export type Severity = "low" | "medium" | "high" | "critical";

export type IncidentStatus =
  | "QUEUED"
  | "RUNNING"
  | "PENDING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "INTERRUPTED";

export interface IncidentSummary {
  incident_id: string;
  title: string;
  severity: Severity | null;
  status: string;
}

export interface IncidentCreateInput {
  title: string;
  severity: Severity;
  service?: string;
  error?: string;
  message?: string;
  target_file?: string;
  files: Record<string, string>;
  auto_run: boolean;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
}

export interface Subtask {
  id: string;
  title: string;
  agent: string;
  detail: string;
  status: string;
}

export interface RootCause {
  hypothesis: string;
  evidence: string[];
  confidence: number;
  memory_refs: string[];
}

export interface PatchFile {
  path: string;
  old_content: string;
  new_content: string;
}

export interface Patch {
  patch_ref: string;
  status: string;
  summary: string;
  diff: string;
  review_reason: string | null;
  files: PatchFile[];
}

export interface Verification {
  ok: boolean;
  output: string;
  failures: string[];
  duration_seconds: number;
  attempt: number;
  coverage: number | null;
}

export interface TimelineStep {
  step_index: number;
  node: string;
  started_at: string;
  finished_at: string;
  status: string;
  summary: string;
  snapshot_ref: string | null;
  duration_seconds: number;
}

export interface IncidentState {
  incident_id: string;
  run_id: string;
  correlation_id: string;
  status: IncidentStatus;
  created_at: string;
  completed_at: string | null;
  step_index: number;
  incident: Record<string, unknown>;
  subtasks: Subtask[];
  root_cause: RootCause | null;
  patch: Patch | null;
  verification: Verification | null;
  timeline: TimelineStep[];
  memory_snapshots: string[];
  token_usage: TokenUsage;
  verifier_attempt: number;
  verifier_feedback: string | null;
  hitl_approved: boolean;
  error: string | null;
}

export interface IncidentDetail {
  incident: Record<string, unknown>;
  state: IncidentState | { incident_id: string; status: string } | null;
  roi: RoiEntry | null;
}

export interface SnapshotMeta {
  ref: string;
  run_id: string | null;
  step_index: number | null;
  node: string | null;
  summary: string | null;
  status: string | null;
}

export interface SnapshotBlob {
  ref: string;
  run_id: string | null;
  step_index: number | null;
  node: string | null;
  summary: string | null;
  status: string | null;
  state: IncidentState;
}

export interface ForkInput {
  snapshot_ref?: string | null;
  title?: string;
  auto_run?: boolean;
}

export interface AgentInfo {
  name: string;
  label: string;
  description: string;
  model_hint: string;
  sandboxed: boolean;
  deps?: string[];
}

export interface SandboxStats {
  backend: string;
  warm: number;
  in_use: number;
  max_size: number;
}

export interface RoiSummary {
  total_incidents: number;
  resolved: number;
  total_tokens: number;
  total_token_cost: number;
  total_saved_amount: number;
  total_saved_seconds: number;
  avg_duration_seconds: number;
  human_mttr_seconds: number;
  loaded_cost_per_hour: number;
}

export interface RoiEntry {
  incident_id: string;
  status: string;
  duration_seconds: number;
  human_mttr_seconds: number;
  input_tokens: number;
  output_tokens: number;
  token_cost: number;
  saved_seconds: number;
  saved_amount: number;
}

export interface RoiData {
  summary: RoiSummary;
  history: RoiEntry[];
}

export interface EventEnvelope {
  version: number;
  id: string;
  type: string;
  channel: string;
  correlation_id: string | null;
  incident_id: string | null;
  run_id: string | null;
  timestamp: string;
  payload: Record<string, unknown>;
}
