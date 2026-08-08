import { useStore } from "../store";
import Badge, { severityStyle, statusStyle } from "../components/Badge";
import IncidentForm from "../components/IncidentForm";
import IncidentDetail from "./IncidentDetail";

export default function Incidents() {
  const incidents = useStore((s) => s.incidents);
  const selectedId = useStore((s) => s.selectedId);
  const selectIncident = useStore((s) => s.selectIncident);

  return (
    <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
      <div className="space-y-6">
        <IncidentForm />

        <div className="rounded-lg border border-slate-800 bg-panel">
          <div className="border-b border-slate-800 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-200">Incidents</h2>
          </div>
          {incidents.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm text-slate-600">
              No incidents yet — use “Ingest alert” above to kick off your first run.
            </div>
          ) : (
            <ul className="divide-y divide-slate-800">
              {incidents.map((incident) => (
                <li key={incident.incident_id}>
                  <button
                    type="button"
                    onClick={() => selectIncident(incident.incident_id)}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-900 ${
                      selectedId === incident.incident_id ? "bg-slate-900/70" : ""
                    }`}
                  >
                    <Badge label={incident.severity ?? "unknown"} className={severityStyle(incident.severity ?? "unknown")} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm text-slate-200">{incident.title || incident.incident_id}</p>
                      <p className="truncate font-mono text-[11px] text-slate-500">{incident.incident_id}</p>
                    </div>
                    <Badge label={incident.status} className={statusStyle(incident.status)} />
                    {selectedId === incident.incident_id && (
                      <span className="text-xs text-accent">›</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="min-w-0 xl:col-span-2">
        <IncidentDetail />
      </div>
    </div>
  );
}
