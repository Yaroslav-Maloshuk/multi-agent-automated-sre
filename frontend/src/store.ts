import { create } from "zustand";
import * as api from "./lib/api";
import { envelopeMessage } from "./lib/api";
import type {
  AgentInfo,
  EventEnvelope,
  Health,
  IncidentCreateInput,
  IncidentDetail,
  IncidentState,
  IncidentSummary,
  RoiData,
  SandboxStats,
} from "./lib/types";

const BASE_CHANNELS = ["telemetry", "sandbox", "system"];
const INCIDENT_CHANNEL = (id: string) => `incident:${id}`;
const MAX_EVENTS = 600;
const MAX_CONSOLE = 400;

export type View = "overview" | "incidents" | "agents" | "metrics";

export interface ConsoleLine {
  id: string;
  ts: string;
  type: string;
  channel: string;
  incident_id: string | null;
  message: string;
}

let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let manualClose = false;
const pendingSubs = new Set<string>();

export function incidentChannel(id: string): string {
  return INCIDENT_CHANNEL(id);
}

function toLine(env: EventEnvelope): ConsoleLine {
  return {
    id: env.id,
    ts: env.timestamp,
    type: env.type,
    channel: env.channel,
    incident_id: env.incident_id,
    message: envelopeMessage(env),
  };
}

function connectWs(get: () => StoreState, set: (partial: Partial<StoreState>) => void): void {
  if (socket) return;
  manualClose = false;
  const ws = new WebSocket(api.wsUrl(BASE_CHANNELS));
  socket = ws;

  ws.onopen = () => {
    set({ connected: true, bootError: null });
    for (const channel of [...BASE_CHANNELS, ...pendingSubs]) {
      ws.send(JSON.stringify({ action: "subscribe", channel }));
    }
    pendingSubs.clear();
  };

  ws.onmessage = (event) => {
    const env = api.parseEnvelope(event.data as string);
    if (env) get().handleEvent(env);
  };

  ws.onerror = () => {
    // surfaced via onclose
  };

  ws.onclose = () => {
    set({ connected: false });
    socket = null;
    if (manualClose) return;
    const retry = get().retryMs;
    set({ retryMs: Math.min(retry * 2, 15000) });
    reconnectTimer = window.setTimeout(() => {
      set({ retryMs: 1000 });
      connectWs(get, set);
    }, retry);
  };
}

function teardownWs(): void {
  manualClose = true;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (socket) {
    socket.close();
    socket = null;
  }
}

interface StoreState {
  view: View;
  setView: (view: View) => void;

  booted: boolean;
  bootError: string | null;
  bootstrap: () => Promise<void>;
  retryBootstrap: () => void;

  health: Health | null;
  connected: boolean;
  retryMs: number;
  incidents: IncidentSummary[];
  agents: AgentInfo[];
  sandboxes: SandboxStats | null;
  roi: RoiData | null;
  events: EventEnvelope[];
  console: ConsoleLine[];

  selectedId: string | null;
  detail: IncidentDetail | null;
  detailLoading: boolean;
  actionError: string | null;
  clearActionError: () => void;

  selectIncident: (id: string) => void;
  loadDetail: (id: string) => Promise<void>;
  createIncident: (input: IncidentCreateInput) => Promise<string | null>;
  runIncident: (id: string) => Promise<void>;
  reviewIncident: (id: string, action: "approve" | "reject", reason: string) => Promise<void>;
  interruptIncident: (id: string) => Promise<void>;
  resumeIncident: (id: string) => Promise<void>;
  warmSandboxes: () => Promise<void>;

  handleEvent: (env: EventEnvelope) => void;
  fetchIncidents: () => Promise<void>;
  fetchAgents: () => Promise<void>;
  fetchSandboxes: () => Promise<void>;
  fetchRoi: () => Promise<void>;
}

export const useStore = create<StoreState>((set, get) => ({
  view: "overview",
  setView: (view) => set({ view }),

  booted: false,
  bootError: null,
  bootstrap: async () => {
    set({ booted: false, bootError: null });
    try {
      const health = await api.getHealth();
      set({ health });
    } catch (err) {
      set({ bootError: err instanceof Error ? err.message : String(err) });
      return;
    }
    connectWs(get, set);
    await Promise.allSettled([
      get().fetchIncidents(),
      get().fetchAgents(),
      get().fetchSandboxes(),
      get().fetchRoi(),
    ]);
    set({ booted: true });
  },
  retryBootstrap: () => {
    teardownWs();
    set({ retryMs: 1000 });
    void get().bootstrap();
  },

  health: null,
  connected: false,
  retryMs: 1000,
  incidents: [],
  agents: [],
  sandboxes: null,
  roi: null,
  events: [],
  console: [],

  selectedId: null,
  detail: null,
  detailLoading: false,
  actionError: null,
  clearActionError: () => set({ actionError: null }),

  selectIncident: (id) => {
    set({ selectedId: id, actionError: null });
    const channel = INCIDENT_CHANNEL(id);
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "subscribe", channel }));
    } else {
      pendingSubs.add(channel);
    }
    void get().loadDetail(id);
  },

  loadDetail: async (id) => {
    set({ detailLoading: true });
    try {
      const detail = await api.getIncident(id);
      set({ detail, detailLoading: false });
    } catch (err) {
      set({
        detailLoading: false,
        actionError: err instanceof Error ? err.message : String(err),
      });
    }
  },

  createIncident: async (input) => {
    set({ actionError: null });
    try {
      const result = await api.createIncident(input);
      await get().fetchIncidents();
      get().selectIncident(result.incident_id);
      return result.incident_id;
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
      return null;
    }
  },

  runIncident: async (id) => {
    set({ actionError: null });
    try {
      await api.runIncident(id);
      await Promise.all([get().fetchIncidents(), get().loadDetail(id)]);
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
    }
  },

  reviewIncident: async (id, action, reason) => {
    set({ actionError: null });
    try {
      const state = get().detail?.state;
      const patchRef =
        state && "patch" in state
          ? (state as { patch?: { patch_ref?: string } | null }).patch?.patch_ref
          : undefined;
      await api.reviewIncident(id, { action, reason, patch_ref: patchRef || undefined });
      await get().loadDetail(id);
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
    }
  },

  interruptIncident: async (id) => {
    set({ actionError: null });
    try {
      await api.interruptIncident(id);
      await Promise.all([get().fetchIncidents(), get().loadDetail(id)]);
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
    }
  },

  resumeIncident: async (id) => {
    set({ actionError: null });
    try {
      await api.resumeIncident(id);
      await Promise.all([get().fetchIncidents(), get().loadDetail(id)]);
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
    }
  },

  warmSandboxes: async () => {
    set({ actionError: null });
    try {
      await api.warmSandboxes();
      await get().fetchSandboxes();
    } catch (err) {
      set({ actionError: err instanceof Error ? err.message : String(err) });
    }
  },

  handleEvent: (env) => {
    set((s) => ({
      events: [...s.events, env].slice(-MAX_EVENTS),
      console: [...s.console, toLine(env)].slice(-MAX_CONSOLE),
    }));
    const t = env.type;
    if (t === "telemetry.roi") {
      void get().fetchRoi();
    } else if (t.startsWith("sandbox.")) {
      void get().fetchSandboxes();
    } else if (t === "memory.snapshot") {
      const payloadState = env.payload.state;
      if (
        payloadState &&
        typeof payloadState === "object" &&
        "timeline" in payloadState &&
        env.incident_id === get().selectedId
      ) {
        const snapshot = payloadState as IncidentState;
        set((s) => ({
          detail: s.detail ? { ...s.detail, state: snapshot } : s.detail,
        }));
      }
    } else if (
      t.startsWith("incident.") ||
      t.startsWith("graph.") ||
      t.startsWith("hitl.") ||
      t === "patch.generated" ||
      t === "agent.log" ||
      t === "agent.token_usage"
    ) {
      void get().fetchIncidents();
      if (env.incident_id && env.incident_id === get().selectedId) {
        void get().loadDetail(env.incident_id);
      }
    }
  },

  fetchIncidents: async () => {
    try {
      const result = await api.listIncidents();
      set({ incidents: result.incidents });
    } catch {
      // transient; retried on next event / refresh
    }
  },

  fetchAgents: async () => {
    try {
      const result = await api.listAgents();
      set({ agents: result.agents });
    } catch {
      // transient
    }
  },

  fetchSandboxes: async () => {
    try {
      set({ sandboxes: await api.getSandboxes() });
    } catch {
      // transient
    }
  },

  fetchRoi: async () => {
    try {
      set({ roi: await api.getRoi() });
    } catch {
      // transient
    }
  },
}));
