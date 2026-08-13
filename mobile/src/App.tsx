import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  createIncident,
  fetchIncidentDetail,
  fetchIncidents,
  interruptRun,
  reviewIncident,
  startRun,
  type IncidentSummary,
} from './api';
import { useEventStream } from './ws';

export default function App() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ incident?: unknown; state?: unknown; roi?: unknown } | null>(null);

  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');

  const { events, connected } = useEventStream();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchIncidents();
      setIncidents(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load incidents');
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    try {
      const d = await fetchIncidentDetail(id);
      setDetail(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load incident');
    }
  }, []);

  const selectIncident = useCallback(
    (id: string) => {
      setSelectedId(id);
      loadDetail(id);
    },
    [loadDetail],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Refresh the list whenever the live stream reports activity on an incident.
  useEffect(() => {
    const timer = setTimeout(() => {
      const touched = events.some((ev) => typeof ev.incident_id === 'string');
      if (touched) {
        load();
        if (selectedId) loadDetail(selectedId);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [events, load, loadDetail, selectedId]);

  const run = async (op: () => Promise<unknown>) => {
    if (!selectedId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await op();
      await loadDetail(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const onCreate = async () => {
    if (!title.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createIncident(title.trim(), summary.trim());
      setTitle('');
      setSummary('');
      await load();
      selectIncident(created.incident_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Create failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>auto-sre mobile</Text>
        <View style={styles.connectionRow}>
          <View style={[styles.dot, connected ? styles.dotOnline : styles.dotOffline]} />
          <Text style={styles.connectionText}>
            {connected ? 'stream connected' : 'stream offline'}
          </Text>
        </View>
      </View>

      {error && <Text style={styles.error}>{error}</Text>}

      <View style={styles.createBox}>
        <Text style={styles.sectionTitle}>New incident</Text>
        <TextInput
          style={styles.input}
          value={title}
          onChangeText={setTitle}
          placeholder="Title (e.g. payment latency)"
          placeholderTextColor="#5c6470"
        />
        <TextInput
          style={styles.input}
          value={summary}
          onChangeText={setSummary}
          placeholder="Summary (optional)"
          placeholderTextColor="#5c6470"
        />
        <Pressable
          style={[styles.button, styles.buttonPrimary, (!title.trim() || busy) && styles.buttonDisabled]}
          onPress={onCreate}
          disabled={!title.trim() || busy}
        >
          <Text style={styles.buttonText}>{busy ? 'Working…' : 'Create'}</Text>
        </Pressable>
      </View>

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Incidents</Text>
        <Pressable onPress={load} style={styles.refreshButton}>
          <Text style={styles.refreshText}>Refresh</Text>
        </Pressable>
      </View>

      {loading ? (
        <ActivityIndicator color="#7c8a9b" />
      ) : (
        <FlatList
          data={incidents}
          keyExtractor={(item) => item.incident_id}
          renderItem={({ item }) => (
            <Pressable
              onPress={() => selectIncident(item.incident_id)}
              style={[styles.incident, selectedId === item.incident_id && styles.incidentSelected]}
            >
              <Text style={styles.incidentId}>{item.incident_id}</Text>
              <View style={styles.incidentMeta}>
                <Text style={styles.severity}>{item.severity}</Text>
                <Text style={styles.status}>{item.status}</Text>
              </View>
            </Pressable>
          )}
          style={styles.incidentsList}
        />
      )}

      {selectedId && (
        <View style={styles.detailBox}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Incident detail</Text>
            <Text style={styles.dim}>{selectedId}</Text>
          </View>
          <View style={styles.actions}>
            <Pressable
              style={[styles.button, styles.buttonPrimary, busy && styles.buttonDisabled]}
              onPress={() => run(() => startRun(selectedId))}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Run</Text>
            </Pressable>
            <Pressable
              style={[styles.button, busy && styles.buttonDisabled]}
              onPress={() => run(() => reviewIncident(selectedId, 'approve'))}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Approve</Text>
            </Pressable>
            <Pressable
              style={[styles.button, busy && styles.buttonDisabled]}
              onPress={() => run(() => reviewIncident(selectedId, 'reject'))}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Reject</Text>
            </Pressable>
            <Pressable
              style={[styles.button, styles.buttonDanger, busy && styles.buttonDisabled]}
              onPress={() => run(() => interruptRun(selectedId))}
              disabled={busy}
            >
              <Text style={styles.buttonText}>Interrupt</Text>
            </Pressable>
          </View>
          {detail?.state ? (
            <Text numberOfLines={6} style={styles.detailJson}>
              {JSON.stringify(detail.state)}
            </Text>
          ) : (
            <Text style={styles.dim}>No graph state yet.</Text>
          )}
        </View>
      )}

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Event stream</Text>
      </View>
      <FlatList
        data={[...events].reverse()}
        keyExtractor={(_, index) => String(index)}
        renderItem={({ item }) => (
          <Text numberOfLines={2} style={styles.event}>
            {JSON.stringify(item)}
          </Text>
        )}
        style={styles.eventsList}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#0d1117',
    paddingTop: 64,
    paddingHorizontal: 16,
  },
  header: {
    marginBottom: 12,
  },
  title: {
    color: '#e6edf3',
    fontSize: 22,
    fontWeight: '700',
  },
  connectionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: 6,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    marginRight: 6,
  },
  dotOnline: {
    backgroundColor: '#3fb950',
  },
  dotOffline: {
    backgroundColor: '#f85149',
  },
  connectionText: {
    color: '#7c8a9b',
    fontSize: 12,
  },
  createBox: {
    backgroundColor: '#161b22',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  input: {
    backgroundColor: '#0d1117',
    borderColor: '#232833',
    borderWidth: 1,
    borderRadius: 6,
    color: '#e6edf3',
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontSize: 13,
    marginBottom: 8,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 8,
  },
  sectionTitle: {
    color: '#e6edf3',
    fontSize: 16,
    fontWeight: '600',
  },
  refreshButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#21262d',
    borderRadius: 6,
  },
  refreshText: {
    color: '#58a6ff',
    fontSize: 13,
  },
  incidentsList: {
    flex: 1,
    marginBottom: 8,
  },
  incident: {
    backgroundColor: '#161b22',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  incidentSelected: {
    borderColor: '#58a6ff',
    borderWidth: 1,
  },
  incidentId: {
    color: '#e6edf3',
    fontSize: 14,
    fontWeight: '600',
  },
  incidentMeta: {
    flexDirection: 'row',
    marginTop: 6,
  },
  severity: {
    color: '#f0883e',
    fontSize: 12,
    marginRight: 8,
  },
  status: {
    color: '#7c8a9b',
    fontSize: 12,
  },
  error: {
    color: '#f85149',
    marginBottom: 8,
  },
  eventsList: {
    maxHeight: 180,
    marginBottom: 16,
  },
  event: {
    color: '#8b949e',
    fontSize: 12,
    fontFamily: 'monospace',
    marginBottom: 4,
  },
  detailBox: {
    backgroundColor: '#161b22',
    borderRadius: 8,
    padding: 12,
    marginBottom: 8,
  },
  dim: {
    color: '#7c8a9b',
    fontSize: 12,
  },
  actions: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 10,
    flexWrap: 'wrap',
  },
  button: {
    backgroundColor: '#21262d',
    borderRadius: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  buttonPrimary: {
    backgroundColor: '#2563eb',
  },
  buttonDanger: {
    backgroundColor: '#7f1d1d',
  },
  buttonDisabled: {
    opacity: 0.55,
  },
  buttonText: {
    color: '#e6edf3',
    fontSize: 13,
    fontWeight: '600',
  },
  detailJson: {
    color: '#c9cdd6',
    fontSize: 12,
    fontFamily: 'monospace',
  },
});
