import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useStore } from "../store";
import { fmtDuration, fmtMoney, fmtTokens } from "../lib/format";

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-panel p-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-100">{value}</p>
    </div>
  );
}

export default function Metrics() {
  const roi = useStore((s) => s.roi);
  const summary = roi?.summary;

  const chartData =
    roi?.history.map((entry, index) => ({
      name: `#${index + 1}`,
      saved: entry.saved_amount,
      cost: entry.token_cost,
    })) ?? [];

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Incidents" value={String(summary?.total_incidents ?? 0)} />
        <StatCard label="Resolved" value={`${summary?.resolved ?? 0}`} />
        <StatCard label="Tokens" value={fmtTokens(summary?.total_tokens ?? 0)} />
        <StatCard label="Net savings" value={fmtMoney(summary?.total_saved_amount ?? 0)} />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Token cost" value={fmtMoney(summary?.total_token_cost ?? 0)} />
        <StatCard label="Time saved" value={fmtDuration(summary?.total_saved_seconds ?? 0)} />
        <StatCard label="Avg resolution" value={fmtDuration(summary?.avg_duration_seconds ?? 0)} />
        <StatCard label="Human MTTR baseline" value={fmtDuration(summary?.human_mttr_seconds ?? 0)} />
      </div>

      <div className="rounded-lg border border-slate-800 bg-panel p-4">
        <p className="mb-3 text-[11px] font-medium uppercase tracking-wider text-slate-500">Savings vs token cost per incident</p>
        {chartData.length === 0 ? (
          <div className="flex h-56 items-center justify-center text-sm text-slate-600">No ROI history yet.</div>
        ) : (
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="saved" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#34d399" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="cost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#f87171" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#f87171" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                <XAxis dataKey="name" stroke="#4b5563" fontSize={11} tickLine={false} />
                <YAxis stroke="#4b5563" fontSize={11} tickLine={false} width={60} />
                <Tooltip
                  contentStyle={{ background: "#0b1020", border: "1px solid #1f2937", borderRadius: 8, fontSize: 12 }}
                  formatter={(value: number | string) => fmtMoney(Number(value))}
                />
                <Area type="monotone" dataKey="saved" name="saved" stroke="#34d399" fill="url(#saved)" strokeWidth={2} />
                <Area type="monotone" dataKey="cost" name="cost" stroke="#f87171" fill="url(#cost)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="rounded-lg border border-slate-800 bg-panel">
        <div className="border-b border-slate-800 px-4 py-2.5">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Ledger</h3>
        </div>
        {!roi?.history.length ? (
          <div className="px-4 py-10 text-center text-sm text-slate-600">No ledger entries yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-[10px] uppercase tracking-wider text-slate-500">
                  <th className="px-4 py-2 font-medium">Incident</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 text-right font-medium">Duration</th>
                  <th className="px-4 py-2 text-right font-medium">Tokens</th>
                  <th className="px-4 py-2 text-right font-medium">Cost</th>
                  <th className="px-4 py-2 text-right font-medium">Saved</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/70">
                {roi.history
                  .slice()
                  .reverse()
                  .map((entry) => (
                    <tr key={entry.incident_id} className="hover:bg-slate-900/60">
                      <td className="px-4 py-2 font-mono text-[11px] text-slate-300">{entry.incident_id}</td>
                      <td className="px-4 py-2 text-slate-400">{entry.status}</td>
                      <td className="px-4 py-2 text-right text-slate-400">{fmtDuration(entry.duration_seconds)}</td>
                      <td className="px-4 py-2 text-right text-slate-400">
                        {fmtTokens(entry.input_tokens + entry.output_tokens)}
                      </td>
                      <td className="px-4 py-2 text-right text-slate-400">{fmtMoney(entry.token_cost)}</td>
                      <td className="px-4 py-2 text-right font-medium text-emerald-300">{fmtMoney(entry.saved_amount)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
