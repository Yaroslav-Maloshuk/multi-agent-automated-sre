const STATUS_STYLES: Record<string, string> = {
  QUEUED: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  RUNNING: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  PENDING_APPROVAL: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  COMPLETED: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  FAILED: "bg-red-500/15 text-red-300 border-red-500/30",
  CANCELLED: "bg-red-500/15 text-red-300 border-red-500/30",
  INTERRUPTED: "bg-amber-500/15 text-amber-300 border-amber-500/30",
};

const SEVERITY_STYLES: Record<string, string> = {
  low: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  medium: "bg-cyan-500/15 text-cyan-300 border-cyan-500/30",
  high: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  critical: "bg-red-500/15 text-red-300 border-red-500/30",
};

const EVENT_STYLES: Record<string, string> = {
  connected: "text-emerald-400",
  subscribed: "text-cyan-400",
  "heartbeat.ping": "text-slate-500",
  "incident.created": "text-emerald-400",
  "incident.updated": "text-slate-300",
  "graph.started": "text-cyan-400",
  "graph.step.started": "text-slate-300",
  "graph.step.completed": "text-slate-400",
  "graph.status.changed": "text-amber-300",
  "graph.completed": "text-emerald-400",
  "graph.failed": "text-red-400",
  "graph.interrupted": "text-amber-400",
  "graph.resumed": "text-cyan-400",
  "agent.started": "text-cyan-300",
  "agent.step": "text-slate-300",
  "agent.log": "text-slate-400",
  "agent.token_usage": "text-violet-300",
  "patch.generated": "text-violet-300",
  "patch.typing": "text-violet-300",
  "patch.diff": "text-violet-300",
  "patch.applied": "text-emerald-400",
  "hitl.pending_approval": "text-amber-300",
  "hitl.review_decided": "text-emerald-400",
  "sandbox.acquired": "text-slate-400",
  "sandbox.released": "text-slate-400",
  "sandbox.stdout": "text-slate-400",
  "memory.snapshot": "text-slate-400",
  "telemetry.roi": "text-violet-300",
  error: "text-red-400",
  pong: "text-slate-500",
};

export function statusStyle(status: string): string {
  return STATUS_STYLES[status] ?? "bg-slate-500/15 text-slate-300 border-slate-500/30";
}

export function severityStyle(severity: string): string {
  return SEVERITY_STYLES[severity] ?? "bg-slate-500/15 text-slate-300 border-slate-500/30";
}

export function eventStyle(type: string): string {
  return EVENT_STYLES[type] ?? "text-slate-400";
}

interface BadgeProps {
  label: string;
  className: string;
}

export default function Badge({ label, className }: BadgeProps) {
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium tracking-wide ${className}`}
    >
      {label}
    </span>
  );
}
