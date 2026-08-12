import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { fetchIncidents, type IncidentSummary } from './api';
import { useEventStream } from './ws';

export default function App() {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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

  useEffect(() => {
    load();
  }, [load]);

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

      <View style={styles.sectionHeader}>
        <Text style={styles.sectionTitle}>Incidents</Text>
        <Pressable onPress={load} style={styles.refreshButton}>
          <Text style={styles.refreshText}>Refresh</Text>
        </Pressable>
      </View>

      {loading ? (
        <ActivityIndicator color="#7c8a9b" />
      ) : error ? (
        <Text style={styles.error}>{error}</Text>
      ) : (
        <FlatList
          data={incidents}
          keyExtractor={(item) => item.incident_id}
          renderItem={({ item }) => (
            <View style={styles.incident}>
              <Text style={styles.incidentId}>{item.incident_id}</Text>
              <View style={styles.incidentMeta}>
                <Text style={styles.severity}>{item.severity}</Text>
                <Text style={styles.status}>{item.status}</Text>
              </View>
            </View>
          )}
          style={styles.incidentsList}
        />
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
    marginBottom: 16,
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
    maxHeight: 220,
    marginBottom: 16,
  },
  event: {
    color: '#8b949e',
    fontSize: 12,
    fontFamily: 'monospace',
    marginBottom: 4,
  },
});
