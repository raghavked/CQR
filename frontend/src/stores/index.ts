/**
 * CQR Zustand Stores — one per domain.
 * State library choice: Zustand (lightweight, minimal boilerplate).
 */

import { create } from 'zustand';
import type { Project, Task, KGNode, KGEdge, SecurityFinding, VaultKey } from '../api/client';

// ── Session Store ──────────────────────────────────────────────────────────

interface SessionState {
  activeProject: Project | null;
  isBackendOnline: boolean;
  agentState: 'idle' | 'working' | 'blocked';
  sessionId: string | null;
  setActiveProject: (p: Project | null) => void;
  setBackendOnline: (v: boolean) => void;
  setAgentState: (s: 'idle' | 'working' | 'blocked') => void;
  setSessionId: (id: string | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  activeProject: null,
  isBackendOnline: true,
  agentState: 'idle',
  sessionId: null,
  setActiveProject: (p) => set({ activeProject: p }),
  setBackendOnline: (v) => set({ isBackendOnline: v }),
  setAgentState: (s) => set({ agentState: s }),
  setSessionId: (id) => set({ sessionId: id }),
}));

// ── KG Store ───────────────────────────────────────────────────────────────

interface KGState {
  nodes: KGNode[];
  edges: KGEdge[];
  selectedNodeId: string | null;
  loading: boolean;
  error: string | null;
  setGraph: (nodes: KGNode[], edges: KGEdge[]) => void;
  setSelectedNode: (id: string | null) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
}

export const useKGStore = create<KGState>((set) => ({
  nodes: [],
  edges: [],
  selectedNodeId: null,
  loading: false,
  error: null,
  setGraph: (nodes, edges) => set({ nodes, edges }),
  setSelectedNode: (id) => set({ selectedNodeId: id }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e }),
}));

// ── LSM Store ──────────────────────────────────────────────────────────────

interface LSMState {
  proximityNodes: Array<{ node_id: string; node_type: string; snippet: string; proximity_score: number | null }>;
  threshold: number;
  query: string;
  loading: boolean;
  error: string | null;
  setProximityNodes: (nodes: LSMState['proximityNodes']) => void;
  setThreshold: (v: number) => void;
  setQuery: (q: string) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
}

export const useLSMStore = create<LSMState>((set) => ({
  proximityNodes: [],
  threshold: 0.7,
  query: '',
  loading: false,
  error: null,
  setProximityNodes: (nodes) => set({ proximityNodes: nodes }),
  setThreshold: (v) => set({ threshold: v }),
  setQuery: (q) => set({ query: q }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e }),
}));

// ── Security Store ─────────────────────────────────────────────────────────

interface ScanState {
  findings: SecurityFinding[];
  selectedFindingId: string | null;
  scanning: boolean;
  scanProgress: number;
  error: string | null;
  setFindings: (f: SecurityFinding[]) => void;
  setSelectedFinding: (id: string | null) => void;
  setScanning: (v: boolean) => void;
  setScanProgress: (p: number) => void;
  setError: (e: string | null) => void;
}

export const useScanStore = create<ScanState>((set) => ({
  findings: [],
  selectedFindingId: null,
  scanning: false,
  scanProgress: 0,
  error: null,
  setFindings: (f) => set({ findings: f }),
  setSelectedFinding: (id) => set({ selectedFindingId: id }),
  setScanning: (v) => set({ scanning: v }),
  setScanProgress: (p) => set({ scanProgress: p }),
  setError: (e) => set({ error: e }),
}));

// ── Vault Store ────────────────────────────────────────────────────────────

interface VaultState {
  keys: VaultKey[];
  loading: boolean;
  error: string | null;
  setKeys: (k: VaultKey[]) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
}

export const useVaultStore = create<VaultState>((set) => ({
  keys: [],
  loading: false,
  error: null,
  setKeys: (k) => set({ keys: k }),
  setLoading: (v) => set({ loading: v }),
  setError: (e) => set({ error: e }),
}));

// ── Agent Store ────────────────────────────────────────────────────────────

interface AgentState {
  tasks: Task[];
  activeTaskId: string | null;
  activityLog: Array<{ ts: string; type: string; message: string }>;
  addActivity: (type: string, message: string) => void;
  setTasks: (t: Task[]) => void;
  setActiveTask: (id: string | null) => void;
  clearActivity: () => void;
}

export const useAgentStore = create<AgentState>((set) => ({
  tasks: [],
  activeTaskId: null,
  activityLog: [],
  addActivity: (type, message) =>
    set((s) => ({
      activityLog: [
        ...s.activityLog.slice(-199),
        { ts: new Date().toISOString(), type, message },
      ],
    })),
  setTasks: (t) => set({ tasks: t }),
  setActiveTask: (id) => set({ activeTaskId: id }),
  clearActivity: () => set({ activityLog: [] }),
}));
