import { useEffect } from "react";
import { useStore } from "./store";
import type { View } from "./store";
import Overview from "./views/Overview";
import Incidents from "./views/Incidents";
import Agents from "./views/Agents";
import Metrics from "./views/Metrics";

const NAV: { key: View; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "incidents", label: "Incidents" },
  { key: "agents", label: "Agents" },
  { key: "metrics", label: "Metrics" },
];

function BootScreen() {
  const bootError = useStore((s) => s.bootError);
  const retryBootstrap = useStore((s) => s.retryBootstrap);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 px-6 text-center">
      <div className="flex items-center gap-3">
        <span className="h-3 w-3 animate-pulse rounded-full bg-accent" />
        <span className="text-sm text-slate-400">Bootstrapping command center…</span>
      </div>
      {bootError && (
        <div className="max-w-md rounded-lg border border-red-800/60 bg-red-950/30 p-4">
          <p className="text-xs font-medium uppercase tracking-wider text-red-300">Backend unreachable</p>
          <p className="mt-1 text-xs text-slate-300">{bootError}</p>
          <button
            type="button"
            onClick={retryBootstrap}
            className="mt-3 rounded-md border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800"
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const booted = useStore((s) => s.booted);
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const connected = useStore((s) => s.connected);
  const health = useStore((s) => s.health);
  const bootstrap = useStore((s) => s.bootstrap);
  const selectedId = useStore((s) => s.selectedId);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-800 px-6 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold tracking-tight text-white">
            Auto-SRE <span className="text-accent">Command Center</span>
          </h1>
          {selectedId && (
            <span className="hidden rounded-full bg-slate-800/60 px-3 py-1 font-mono text-[11px] text-slate-400 md:inline">
              {selectedId}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {health?.status && (
            <span
              className={`hidden rounded-full px-3 py-1 text-xs font-medium sm:inline ${
                health.status === "ok"
                  ? "bg-emerald-500/10 text-emerald-400"
                  : "bg-red-500/10 text-red-400"
              }`}
            >
              ● {health.status}
            </span>
          )}
          <span
            className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${
              connected ? "bg-emerald-500/10 text-emerald-400" : "bg-amber-500/10 text-amber-400"
            }`}
          >
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${connected ? "bg-emerald-400" : "animate-pulse bg-amber-400"}`} />
            {connected ? "stream live" : "stream reconnecting"}
          </span>
        </div>
      </header>

      {!booted ? (
        <BootScreen />
      ) : (
        <div className="flex min-h-0 flex-1">
          <nav className="flex w-44 shrink-0 flex-col border-r border-slate-800 bg-slate-900/40 px-2 py-3">
            {NAV.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setView(item.key)}
                className={`rounded-md px-3 py-2 text-left text-sm font-medium transition ${
                  view === item.key
                    ? "bg-slate-800 text-accent"
                    : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200"
                }`}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <main className="min-w-0 flex-1 overflow-y-auto p-6">
            {view === "overview" && <Overview />}
            {view === "incidents" && <Incidents />}
            {view === "agents" && <Agents />}
            {view === "metrics" && <Metrics />}
          </main>
        </div>
      )}
    </div>
  );
}
