import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useStore } from "../store";
import Badge from "../components/Badge";
import Console from "../components/Console";
import { fmtDuration, fmtMoney, fmtTokens } from "../lib/format";

function StatCard({ label, value, sub }: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-panel p-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-100">{value}</p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export default function Overview() {
  const health = useStore((s) => s.health);
  const connected = useStore((s) => s.connected);
  const sandboxes = useStore((s) => s.sandboxes);
  const roi = useStore((s) => s.roi);
  const incidents = useStore((s) => s.incidents);
  const consoleLines = useStore((s) => s.console);
  const warmSandboxes = useStore((s) => s.warmSandboxes);

  const deps = health?.dependencies ?? {};
  const depsOk = Object.values(deps).every((v) => v === "ok");
  const chartData =
    roi?.history
      .map((entry, index) => ({
        name: `#${index + 1}`,
        saved: entry.saved_amount,
        tokens: entry.input_tokens + entry.output_tokens,
      }))
      .slice(-20) ?? [];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Gateway"
          value={health?.status ?? "…"}
          sub={
            <span className="flex items-center gap-2">
              <span className={`inline-block h-2 w-2 rounded-full ${health?.status === "ok" ? "bg-emerald-400" : "bg-red-400"}`} />
              {health ? `v${health.version} · ${health.environment}` : "unreachable"}
            </span>
          }
        />
        <StatCard
          label="Event stream"
          value={connected ? "Live" : "Reconnecting"}
          sub={connected ? "WebSocket connected" : "retrying…"}
        />
        <StatCard
          label="Sandbox pool"
          value={`${sandboxes?.warm ?? 0}/${sandboxes?.max_size ?? 0}`}
          sub={
            <span className="flex items-center gap-2">
              <span>{sandboxes ? `${sandboxes.in_use} in use · ${sandboxes.backend}` : "unknown"}</span>
              <button
                type="button"
                onClick={() => void warmSandboxes()}
                className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-cyan-300 hover:border-accent"
              >
                warm
              </button>
            </span>
          }
        />
        <StatCard
          label="Incidents"
          value={String(incidents.length)}
          sub={
            incidents.length > 0
              ? `${incidents.filter((i) => i.status === "COMPLETED").length} completed · ${incidents.filter((i) => i.status === "RUNNING").length} running`
              : "none yet"
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-1">
          <div className="rounded-lg border border-slate-800 bg-panel p-4">
            <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-500">Dependencies</p>
            <ul className="space-y-2 text-sm">
              {Object.entries(deps).length === 0 && <li className="text-slate-600">no probe data</li>}
              {Object.entries(deps).map(([name, state]) => (
                <li key={name} className="flex items-center justify-between">
                  <span className="text-slate-400">{name}</span>
                  <Badge label={state} className={state === "ok" ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" : "bg-red-500/15 text-red-300 border-red-500/30"} />
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-lg border border-slate-800 bg-panel p-4">
            <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-500">ROI ledger</p>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-400">Resolved</dt>
                <dd className="text-slate-200">{roi?.summary.resolved ?? 0} / {roi?.summary.total_incidents ?? 0}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Tokens</dt>
                <dd className="text-slate-200">{fmtTokens(roi?.summary.total_tokens ?? 0)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Token cost</dt>
                <dd className="text-slate-200">{fmtMoney(roi?.summary.total_token_cost ?? 0)}</dd>
              </div>
              <div className="flex justify-between border-t border-slate-800 pt-2">
                <dt className="text-slate-400">Saved</dt>
                <dd className="font-semibold text-emerald-300">{fmtMoney(roi?.summary.total_saved_amount ?? 0)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Avg resolution</dt>
                <dd className="text-slate-200">{fmtDuration(roi?.summary.avg_duration_seconds ?? 0)}</dd>
              </div>
            </dl>
          </div>
        </div>

        <div className="space-y-4 xl:col-span-2">
          <div className="rounded-lg border border-slate-800 bg-panel p-4">
            <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-500">Savings per incident</p>
            {chartData.length === 0 ? (
              <div className="flex h-56 items-center justify-center text-sm text-slate-600">
                No completed incidents yet — ROI history appears here once runs finish.
              </div>
            ) : (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="savedFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                    <XAxis dataKey="name" stroke="#4b5563" fontSize={11} tickLine={false} />
                    <YAxis stroke="#4b5563" fontSize={11} tickLine={false} width={60} />
                    <Tooltip
                      contentStyle={{ background: "#0b1020", border: "1px solid #1f2937", borderRadius: 8, fontSize: 12 }}
                      formatter={(value: number | string) => fmtMoney(Number(value))}
                    />
                    <Area type="monotone" dataKey="saved" stroke="#22d3ee" fill="url(#savedFill)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          <div className="rounded-lg border border-slate-800 bg-panel p-4">
            <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-500">
              Live event stream <span className="text-slate-600">({depsOk ? "system nominal" : "system degraded"})</span>
            </p>
            <Console lines={consoleLines} heightClass="h-72" />
          </div>
        </div>
      </div>
    </div>
  );
}
