import { useState } from "react";
import type { FormEvent } from "react";
import { useStore } from "../store";
import type { Severity } from "../lib/types";

const SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];

const inputClass =
  "w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-accent";

export default function IncidentForm() {
  const createIncident = useStore((s) => s.createIncident);
  const actionError = useStore((s) => s.actionError);
  const clearActionError = useStore((s) => s.clearActionError);

  const [title, setTitle] = useState("");
  const [severity, setSeverity] = useState<Severity>("high");
  const [service, setService] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [message, setMessage] = useState("");
  const [targetFile, setTargetFile] = useState("");
  const [filesJson, setFilesJson] = useState("");
  const [autoRun, setAutoRun] = useState(true);
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    clearActionError();
    let files: Record<string, string> = {};
    if (filesJson.trim()) {
      try {
        const parsed: unknown = JSON.parse(filesJson);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          files = parsed as Record<string, string>;
        } else {
          throw new Error("files must be a JSON object");
        }
      } catch (err) {
        useStore.setState({
          actionError: err instanceof Error ? `Invalid files JSON: ${err.message}` : "Invalid files JSON",
        });
        setSubmitting(false);
        return;
      }
    }
    const id = await createIncident({
      title,
      severity,
      service: service || undefined,
      error: errorMsg || undefined,
      message: message || undefined,
      target_file: targetFile || undefined,
      files,
      auto_run: autoRun,
    });
    setSubmitting(false);
    if (id) {
      setTitle("");
      setService("");
      setErrorMsg("");
      setMessage("");
      setTargetFile("");
      setFilesJson("");
      setOpen(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-800 bg-panel">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-slate-200"
      >
        <span className="flex items-center gap-2">
          <span className="text-accent">+</span> Ingest alert
        </span>
        <span className="text-xs text-slate-500">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <form onSubmit={onSubmit} className="space-y-3 border-t border-slate-800 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Title *</label>
            <input
              className={inputClass}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="p99 latency spike on checkout service"
              required
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Severity</label>
              <select className={inputClass} value={severity} onChange={(e) => setSeverity(e.target.value as Severity)}>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Service</label>
              <input
                className={inputClass}
                value={service}
                onChange={(e) => setService(e.target.value)}
                placeholder="checkout-api"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Error / symptom</label>
            <input
              className={inputClass}
              value={errorMsg}
              onChange={(e) => setErrorMsg(e.target.value)}
              placeholder="TimeoutError: connection to redis timed out after 5s"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-400">Message</label>
            <input
              className={inputClass}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Supporting alert message"
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">Target file</label>
              <input
                className={inputClass}
                value={targetFile}
                onChange={(e) => setTargetFile(e.target.value)}
                placeholder="src/checkout/cache.py"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-400">File snapshot (JSON)</label>
              <input
                className={inputClass}
                value={filesJson}
                onChange={(e) => setFilesJson(e.target.value)}
                placeholder='{"src/app.py": "def handle(): ..."}'
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-400">
            <input
              type="checkbox"
              checked={autoRun}
              onChange={(e) => setAutoRun(e.target.checked)}
              className="h-4 w-4 rounded border-slate-600 bg-slate-900 accent-accent"
            />
            Auto-run the agent graph after ingest
          </label>
          {actionError && <p className="text-xs text-red-400">{actionError}</p>}
          <button
            type="submit"
            disabled={submitting || !title.trim()}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-slate-900 transition hover:bg-cyan-300 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Ingesting…" : "Ingest & run"}
          </button>
        </form>
      )}
    </div>
  );
}
