import { useState } from "react";
import { useStore } from "../store";
import * as api from "../lib/api";
import type { IncidentState, SnapshotBlob } from "../lib/types";
import { fmtClock, fmtDuration, fmtMoney, fmtTokens } from "../lib/format";
import Badge, { statusStyle } from "../components/Badge";
import DiffView from "../components/DiffView";
import TerminalView from "../components/TerminalView";

interface SectionProps {
  title: string;
  children: React.ReactNode;
}

function Section({ title, children }: SectionProps) {
  return (
    <div className="rounded-lg border border-slate-800 bg-panel">
      <div className="border-b border-slate-800 px-4 py-2.5">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{title}</h3>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function CodeBlock({ text }: { text: string }) {
  return (
    <pre className="max-h-72 overflow-auto rounded-md border border-slate-800 bg-black/40 p-3 font-mono text-xs leading-relaxed text-slate-300">
      {text || "—"}
    </pre>
  );
}

function FilePair({ path, oldContent, newContent }: { path: string; oldContent: string; newContent: string }) {
  const language = (() => {
    if (path.endsWith(".py")) return "python";
    if (path.endsWith(".ts") || path.endsWith(".tsx")) return "typescript";
    if (path.endsWith(".js") || path.endsWith(".jsx")) return "javascript";
    if (path.endsWith(".json")) return "json";
    if (path.endsWith(".yml") || path.endsWith(".yaml")) return "yaml";
    if (path.endsWith(".go")) return "go";
    if (path.endsWith(".sh")) return "shell";
    if (path.endsWith(".sql")) return "sql";
    if (path.endsWith(".md")) return "markdown";
    return "plaintext";
  })();
  return (
    <div>
      <p className="mb-1 font-mono text-xs font-medium text-cyan-300">{path}</p>
      <DiffView
        original={oldContent}
        modified={newContent}
        language={language}
        originalLabel="before"
        modifiedLabel="after"
      />
    </div>
  );
}

function isFullState(state: unknown): state is IncidentState {
  return (
    typeof state === "object" &&
    state !== null &&
    "status" in (state as Record<string, unknown>) &&
    "timeline" in (state as Record<string, unknown>) &&
    "subtasks" in (state as Record<string, unknown>)
  );
}

export default function IncidentDetail() {
  const detail = useStore((s) => s.detail);
  const detailLoading = useStore((s) => s.detailLoading);
  const selectedId = useStore((s) => s.selectedId);
  const actionError = useStore((s) => s.actionError);
  const runIncident = useStore((s) => s.runIncident);
  const interruptIncident = useStore((s) => s.interruptIncident);
  const resumeIncident = useStore((s) => s.resumeIncident);
  const reviewIncident = useStore((s) => s.reviewIncident);
  const consoleLines = useStore((s) => s.console);
  const fetchIncidents = useStore((s) => s.fetchIncidents);
  const selectIncident = useStore((s) => s.selectIncident);
  const [reason, setReason] = useState("");
  const [replay, setReplay] = useState<SnapshotBlob | null>(null);
  const [forking, setForking] = useState(false);
  const [forkError, setForkError] = useState<string | null>(null);

  const loadSnapshot = async (ref: string) => {
    if (!selectedId) return;
    setForkError(null);
    try {
      const res = await api.getSnapshot(selectedId, ref);
      setReplay({ ...res.snapshot, ref: res.ref });
    } catch (err) {
      setForkError(err instanceof Error ? err.message : String(err));
    }
  };

  const forkFromReplay = async () => {
    if (!selectedId || !replay) return;
    setForking(true);
    setForkError(null);
    try {
      const res = await api.forkIncident(selectedId, { snapshot_ref: replay.ref, auto_run: true });
      await fetchIncidents();
      setReplay(null);
      selectIncident(res.incident_id);
    } catch (err) {
      setForkError(err instanceof Error ? err.message : String(err));
    } finally {
      setForking(false);
    }
  };

  if (!selectedId) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-600">
        Select an incident to inspect its run.
      </div>
    );
  }

  if (detailLoading && !detail) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        Loading run state…
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-500">
        No incident state available.
      </div>
    );
  }

  const state = detail.state;
  const status = isFullState(state) ? state.status : (state as { status?: string } | null)?.status ?? "QUEUED";
  const title = (detail.incident.title as string | undefined) ?? selectedId;
  const incidentConsole = consoleLines.filter((line) => line.incident_id === selectedId).slice(-250);

  const patch = isFullState(state) ? state.patch : null;
  const pendingApproval = status === "PENDING_APPROVAL" && patch !== null;

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-slate-800 bg-panel p-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="min-w-0 flex-1 truncate text-lg font-semibold text-white">{title}</h2>
          <Badge label={status} className={statusStyle(status)} />
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
          <span className="font-mono">{selectedId}</span>
          {isFullState(state) && (
            <>
              <span>run <span className="font-mono text-slate-400">{state.run_id}</span></span>
              <span>corr <span className="font-mono text-slate-400">{state.correlation_id}</span></span>
              <span>started {fmtClock(state.created_at)}</span>
              <span>step {state.step_index}</span>
            </>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {status === "QUEUED" || status === "FAILED" || status === "CANCELLED" ? (
            <button
              type="button"
              onClick={() => void runIncident(selectedId)}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-slate-900 hover:bg-cyan-300"
            >
              Run graph
            </button>
          ) : null}
          {status === "RUNNING" || status === "PENDING_APPROVAL" ? (
            <button
              type="button"
              onClick={() => void interruptIncident(selectedId)}
              className="rounded-md border border-red-800/60 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-950/40"
            >
              Interrupt
            </button>
          ) : null}
          {status === "INTERRUPTED" || status === "FAILED" ? (
            <button
              type="button"
              onClick={() => void resumeIncident(selectedId)}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800"
            >
              Resume
            </button>
          ) : null}
        </div>
        {actionError && <p className="mt-3 text-xs text-red-400">{actionError}</p>}
      </div>

      {isFullState(state) ? (
        <>
          <Section title="Timeline">
            {state.timeline.length === 0 ? (
              <p className="text-sm text-slate-600">No steps recorded yet.</p>
            ) : (
              <ol className="space-y-3">
                {state.timeline.map((step) => {
                  const active = replay !== null && replay.step_index === step.step_index;
                  return (
                    <li key={step.step_index} className="flex gap-3">
                      <div className="flex flex-col items-center">
                        <span
                          className={`mt-1 h-2.5 w-2.5 rounded-full border ${
                            active ? "border-amber-400 bg-amber-400" : "border-cyan-400/60 bg-cyan-400/20"
                          }`}
                        />
                        <span className="w-px flex-1 bg-slate-800" />
                      </div>
                      <div className="min-w-0 flex-1 pb-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            disabled={!step.snapshot_ref}
                            onClick={() => step.snapshot_ref && void loadSnapshot(step.snapshot_ref)}
                            title={step.snapshot_ref ? "Replay this step / fork from here" : "No snapshot for this step"}
                            className={`text-sm font-medium ${
                              step.snapshot_ref
                                ? "cursor-pointer text-slate-200 underline-offset-2 hover:text-amber-300 hover:underline"
                                : "text-slate-200"
                            }`}
                          >
                            {step.node}
                          </button>
                          <span className="font-mono text-[10px] text-slate-600">#{step.step_index}</span>
                          <span className="ml-auto text-[11px] text-slate-500">
                            {fmtClock(step.finished_at)} · {fmtDuration(step.duration_seconds)}
                          </span>
                        </div>
                        <p className="mt-0.5 text-xs text-slate-400">{step.summary}</p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </Section>

          {replay && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-amber-300">
                  Replaying snapshot #{replay.step_index} · {replay.node}
                </p>
                <p className="mt-0.5 text-xs text-slate-400">{replay.summary}</p>
              </div>
              <button
                type="button"
                onClick={() => void forkFromReplay()}
                disabled={forking}
                className="rounded-md bg-amber-500 px-3 py-1.5 text-xs font-semibold text-slate-900 hover:bg-amber-400 disabled:opacity-50"
              >
                {forking ? "Forking…" : "Fork run from here"}
              </button>
              <button
                type="button"
                onClick={() => setReplay(null)}
                className="rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800"
              >
                Jump to latest
              </button>
              {forkError && <span className="text-xs text-red-400">{forkError}</span>}
            </div>
          )}

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Section title={`Subtasks (${state.subtasks.length})`}>
              {state.subtasks.length === 0 ? (
                <p className="text-sm text-slate-600">No subtasks yet.</p>
              ) : (
                <ul className="grid grid-cols-1 gap-2">
                  {state.subtasks.map((subtask) => (
                    <li key={subtask.id} className="flex items-start gap-2 rounded-md border border-slate-800 bg-black/20 px-3 py-2">
                      <span
                        className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                          subtask.status === "completed"
                            ? "bg-emerald-400"
                            : subtask.status === "running"
                              ? "animate-pulse bg-cyan-400"
                              : subtask.status === "failed"
                                ? "bg-red-400"
                                : "bg-slate-600"
                        }`}
                      />
                      <div className="min-w-0">
                        <p className="text-xs font-medium text-slate-200">{subtask.title}</p>
                        <p className="font-mono text-[10px] text-slate-500">{subtask.agent}</p>
                        {subtask.detail && <p className="mt-0.5 text-[11px] text-slate-400">{subtask.detail}</p>}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            <Section title="Root cause">
              {state.root_cause ? (
                <div className="space-y-3">
                  <p className="text-sm font-medium text-slate-100">{state.root_cause.hypothesis}</p>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Confidence</p>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                      <div
                        className="h-full rounded-full bg-cyan-400"
                        style={{ width: `${Math.round(state.root_cause.confidence * 100)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{Math.round(state.root_cause.confidence * 100)}%</p>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Evidence</p>
                    <ul className="space-y-1">
                      {state.root_cause.evidence.map((e, i) => (
                        <li key={i} className="font-mono text-[11px] text-slate-400">– {e}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-slate-600">Analysis not complete.</p>
              )}
            </Section>
          </div>

          <Section title={patch ? `Patch · ${patch.patch_ref} · ${patch.status}` : "Patch"}>
            {patch ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge label={patch.status} className={statusStyle(patch.status)} />
                  {patch.summary && <p className="text-xs text-slate-400">{patch.summary}</p>}
                </div>
                {patch.review_reason && (
                  <p className="text-xs text-slate-500">Review note: {patch.review_reason}</p>
                )}

                {pendingApproval && (
                  <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-300">
                      Human approval required
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        value={reason}
                        onChange={(e) => setReason(e.target.value)}
                        placeholder="Optional review note…"
                        className="min-w-48 flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs text-slate-200 placeholder-slate-600 outline-none focus:border-accent"
                      />
                      <button
                        type="button"
                        onClick={() => void reviewIncident(selectedId, "approve", reason)}
                        className="rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-slate-900 hover:bg-emerald-400"
                      >
                        Approve & verify
                      </button>
                      <button
                        type="button"
                        onClick={() => void reviewIncident(selectedId, "reject", reason)}
                        className="rounded-md border border-red-800/60 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-950/40"
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                )}

                {patch.diff && (
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Unified diff</p>
                    <CodeBlock text={patch.diff} />
                  </div>
                )}
                {patch.files.map((file) => (
                  <FilePair key={file.path} path={file.path} oldContent={file.old_content} newContent={file.new_content} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-600">No patch generated yet.</p>
            )}
          </Section>

          <Section title="Verification">
            {state.verification ? (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    label={state.verification.ok ? "PASS" : "FAIL"}
                    className={state.verification.ok ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" : "bg-red-500/15 text-red-300 border-red-500/30"}
                  />
                  <span className="text-xs text-slate-500">
                    attempt {state.verification.attempt} · {fmtDuration(state.verification.duration_seconds)}
                  </span>
                  {state.verification.coverage !== null && (
                    <span className="text-xs text-slate-500">coverage {state.verification.coverage}%</span>
                  )}
                </div>
                {state.verification.failures.length > 0 && (
                  <ul className="space-y-1">
                    {state.verification.failures.map((f, i) => (
                      <li key={i} className="font-mono text-[11px] text-red-300">– {f}</li>
                    ))}
                  </ul>
                )}
                <div>
                  <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Output</p>
                  <CodeBlock text={state.verification.output} />
                </div>
              </div>
            ) : (
              <p className="text-sm text-slate-600">Not verified yet.</p>
            )}
          </Section>

          <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
            <Section title="Token usage">
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between">
                  <dt className="text-slate-400">Input</dt>
                  <dd className="text-slate-200">{fmtTokens(state.token_usage.input_tokens)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-400">Output</dt>
                  <dd className="text-slate-200">{fmtTokens(state.token_usage.output_tokens)}</dd>
                </div>
                <div className="flex justify-between border-t border-slate-800 pt-2">
                  <dt className="text-slate-400">Total</dt>
                  <dd className="font-semibold text-slate-100">
                    {fmtTokens(state.token_usage.input_tokens + state.token_usage.output_tokens)}
                  </dd>
                </div>
              </dl>
              {detail.roi && (
                <dl className="mt-4 space-y-2 border-t border-slate-800 pt-3 text-sm">
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Token cost</dt>
                    <dd className="text-slate-200">{fmtMoney(detail.roi.token_cost)}</dd>
                  </div>
                  <div className="flex justify-between">
                    <dt className="text-slate-400">Saved vs MTTR</dt>
                    <dd className="font-semibold text-emerald-300">{fmtMoney(detail.roi.saved_amount)}</dd>
                  </div>
                </dl>
              )}
              {state.error && <p className="mt-4 text-xs text-red-400">error: {state.error}</p>}
            </Section>

            <Section title="Run console">
              <TerminalView
                lines={incidentConsole.map((line) => line.message)}
                height="h-64"
                empty="Waiting for run events on this incident…"
              />
            </Section>
          </div>
        </>
      ) : (
        <div className="rounded-lg border border-slate-800 bg-panel p-8 text-center text-sm text-slate-500">
          {status === "QUEUED" ? (
            <>Incident queued — press “Run graph” to start the agent pipeline.</>
          ) : (
            <>Incident not yet in a full run state (status: {status}).</>
          )}
        </div>
      )}
    </div>
  );
}
