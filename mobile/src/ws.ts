import { useEffect, useRef, useState } from 'react';

const WS_URL =
  process.env['WS_URL'] ??
  process.env['EXPO_PUBLIC_WS_URL'] ??
  'ws://localhost:8000';

const STREAM_URL = `${WS_URL}/ws/stream?channels=*`;
const MAX_EVENTS = 50;
const MAX_BACKOFF = 30000;

export interface StreamEvent {
  [key: string]: unknown;
}

export function useEventStream() {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let disposed = false;

    const connect = () => {
      const ws = new WebSocket(STREAM_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        retriesRef.current = 0;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(event.data as string);
        } catch {
          return;
        }
        if (parsed && typeof parsed === 'object') {
          setEvents((prev) => [...prev.slice(-(MAX_EVENTS - 1)), parsed as StreamEvent]);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (disposed) {
          return;
        }
        const delay = Math.min(1000 * 2 ** retriesRef.current, MAX_BACKOFF);
        retriesRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      disposed = true;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
      socketRef.current?.close();
    };
  }, []);

  return { events, connected };
}
