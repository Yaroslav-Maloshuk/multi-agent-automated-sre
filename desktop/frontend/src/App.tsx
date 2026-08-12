import { useCallback, useEffect, useState } from "react";

declare global {
  interface Window {
    go: any;
  }
}

interface Incident {
  IncidentID: string;
  Status: string;
  Severity: string;
}

type ConnectionStatus = "connecting" | "online" | "offline";

const severityClass = (severity: string): string =>
  severity.toLowerCase().replace(/[^a-z0-9]/g, "");

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setStatus("connecting");
    try {
      const items = await window.go.main.App.Incidents();
      setIncidents(items ?? []);
      setStatus("online");
    } catch (err) {
      setStatus("offline");
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="app">
      <header className="app-header">
        <h1>auto-sre-desktop</h1>
        <span className={`status status-${status}`}>
          {status === "connecting"
            ? "connecting…"
            : status === "online"
              ? "gateway online"
              : "gateway offline"}
        </span>
        <button className="refresh" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      {error && <div className="error">Failed to load incidents: {error}</div>}

      <main>
        <table>
          <thead>
            <tr>
              <th>Incident ID</th>
              <th>Status</th>
              <th>Severity</th>
            </tr>
          </thead>
          <tbody>
            {incidents.length === 0 && !loading ? (
              <tr>
                <td colSpan={3} className="empty">
                  No incidents found.
                </td>
              </tr>
            ) : (
              incidents.map((inc) => (
                <tr key={inc.IncidentID}>
                  <td className="mono">{inc.IncidentID}</td>
                  <td>{inc.Status}</td>
                  <td>
                    <span className={`badge ${severityClass(inc.Severity)}`}>
                      {inc.Severity}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </main>
    </div>
  );
}
