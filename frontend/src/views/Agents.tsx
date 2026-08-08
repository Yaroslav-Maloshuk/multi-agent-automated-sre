import { useStore } from "../store";
import Badge from "../components/Badge";

export default function Agents() {
  const agents = useStore((s) => s.agents);

  return (
    <div className="space-y-6">
      <div className="rounded-lg border border-slate-800 bg-panel px-4 py-3">
        <h2 className="text-sm font-semibold text-slate-200">Agent catalog</h2>
        <p className="text-xs text-slate-500">
          The stateful DAG: alert_ingest → planner → log_analyzer → code_fixer → hitl_gate → verifier → deploy
        </p>
      </div>

      {agents.length === 0 ? (
        <div className="rounded-lg border border-slate-800 bg-panel px-4 py-10 text-center text-sm text-slate-600">
          No agent catalog data returned.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {agents.map((agent) => (
            <div key={agent.name} className="rounded-lg border border-slate-800 bg-panel p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-semibold text-slate-100">{agent.label}</h3>
                <Badge
                  label={agent.sandboxed ? "sandboxed" : "host"}
                  className={
                    agent.sandboxed
                      ? "bg-violet-500/15 text-violet-300 border-violet-500/30"
                      : "bg-slate-500/15 text-slate-300 border-slate-500/30"
                  }
                />
              </div>
              <p className="mt-0.5 font-mono text-[10px] text-slate-500">{agent.name}</p>
              <p className="mt-2 text-xs text-slate-400">{agent.description}</p>
              <div className="mt-3 space-y-1 text-[11px]">
                <p className="text-slate-500">
                  model <span className="text-slate-300">{agent.model_hint}</span>
                </p>
                {agent.deps && agent.deps.length > 0 && (
                  <p className="text-slate-500">
                    deps <span className="text-slate-300">{agent.deps.join(", ")}</span>
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
