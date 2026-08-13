import { useCallback, useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    go: any;
    runtime: any;
  }
}

interface Incident {
  IncidentID: string;
  Title: any;
  Status: string;
  Severity: string;
}

interface IncidentDetail {
  incident?: Record<string, any>;
  state?: Record<string, any>;
  roi?: Record<string, any> | null;
}

type ConnectionStatus = "connecting" | "online" | "offline";

const severityClass = (severity: string): string =>
  severity.toLowerCase().replace(/[^a-z0-9]/g, "");

export default function App() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<IncidentDetail | null>(null);

  const [events, setEvents] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");

  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

  const loadDetail = useCallback(async (id: string) => {
    try {
      const d = await window.go.main.App.IncidentDetail(id);
      setDetail(d ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const selectIncident = useCallback(
    (id: string) => {
      setSelected(id);
      loadDetail(id);
    },
    [loadDetail],
  );

  // Live gateway events → stream panel + refreshed incident list.
  useEffect(() => {
    const onEvent = (ev: any) => {
      setEvents((prev) => [...prev.slice(-99), ev]);
      if (ev && ev.incident_id) {
        if (refreshTimer.current) clearTimeout(refreshTimer.current);
        refreshTimer.current = setTimeout(() => {
          load();
          if (selected && ev.incident_id === selected) loadDetail(selected);
        }, 400);
      }
    };
    const onConnection = (ev: any) => {
      setStatus(ev && ev.connected ? "online" : "offline");
    };
    window.runtime.EventsOn("sre:event", onEvent);
    window.runtime.EventsOn("sre:connection", onConnection);
    return () => {
      window.runtime.EventsOff("sre:event");
      window.runtime.EventsOff("sre:connection");
    };
  }, [load, loadDetail, selected]);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (op: () => Promise<any>, label: string, id: string) => {
    setBusy(label);
    setError(null);
    try {
      await op();
      await loadDetail(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const createIncident = async () => {
    if (!title.trim()) return;
    setBusy("create");
    setError(null);
    try {
      const inc = await window.go.main.App.CreateIncident(title.trim(), summary.trim());
      setTitle("");
      setSummary("");
      await load();
      if (inc && inc.IncidentID) selectIncident(inc.IncidentID);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const review = (action: string) => {
    if (!selected) return;
    run(() => window.go.main.App.Review(selected, action), `review:${action}`, selected);
  };

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

      {error && <div className="error">{error}</div>}

      <section className="create">
        <h2>New incident</h2>
        <div className="create-row">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title (e.g. payment service latency)"
          />
          <input
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="Summary / alert body (optional)"
          />
          <button onClick={createIncident} disabled={busy !== null || !title.trim()}>
            {busy === "create" ? "Creating…" : "Create"}
          </button>
        </div>
      </section>

      <main className="layout">
        <section className="panel">
          <h2>Incidents</h2>
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
                  <tr
                    key={inc.IncidentID}
                    className={selected === inc.IncidentID ? "selected" : ""}
                    onClick={() => selectIncident(inc.IncidentID)}
                  >
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
        </section>

        <section className="panel">
          <h2>
            {selected ? "Incident detail" : "Select an incident"}
            {selected && <span className="mono dim">{selected}</span>}
          </h2>
          {selected && (
            <>
              <div className="actions">
                <button
                  className="primary"
                  disabled={busy !== null}
                  onClick={() =>
                    run(() => window.go.main.App.StartRun(selected), "run", selected)
                  }
                >
                  {busy === "run" ? "Running…" : "Run graph"}
                </button>
                <button disabled={busy !== null} onClick={() => review("approve")}>
                  {busy === "review:approve" ? "Approving…" : "Approve"}
                </button>
                <button disabled={busy !== null} onClick={() => review("reject")}>
                  {busy === "review:reject" ? "Rejecting…" : "Reject"}
                </button>
                <button
                  className="danger"
                  disabled={busy !== null}
                  onClick={() =>
                    run(() => window.go.main.App.Interrupt(selected), "interrupt", selected)
                  }
                >
                  {busy === "interrupt" ? "Interrupting…" : "Interrupt"}
                </button>
              </div>
              <div className="detail">
                {detail?.state && (
                  <>
                    <h3>Graph state</h3>
                    <pre>{JSON.stringify(detail.state, null, 2)}</pre>
                  </>
                )}
                {detail?.roi && (
                  <>
                    <h3>ROI</h3>
                    <pre>{JSON.stringify(detail.roi, null, 2)}</pre>
                  </>
                )}
                {detail?.incident && (
                  <>
                    <h3>Incident payload</h3>
                    <pre>{JSON.stringify(detail.incident, null, 2)}</pre>
                  </>
                )}
              </div>
            </>
          )}
        </section>
      </main>

      <section className="panel stream">
        <h2>Live stream (NATS → WS)</h2>
        <div className="stream-body">
          {events.length === 0 ? (
            <div className="empty">Waiting for events…</div>
          ) : (
            [...events].reverse().map((ev, i) => (
              <div key={events.length - i} className="stream-line">
                <span className="mono dim">{(ev.type ?? ev.t) || "event"}</span>
                <span className="mono">{ev.incident_id ?? ""}</span>
                <pre>{JSON.stringify(ev.payload ?? ev)}</pre>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
