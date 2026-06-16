/**
 * CQR API Client
 * All routes confirmed against live backend (CP-1..CP-5).
 * Base URL: http://localhost:8000 (Orchestration)
 * KG:       http://localhost:8001
 * LSM:      http://localhost:8002
 * Exec:     http://localhost:8003
 * Vault:    http://localhost:8004
 * Agent:    http://localhost:8005
 * Security: http://localhost:8006
 */

const ORCH = 'http://localhost:8000';
const KG   = 'http://localhost:8001';
const LSM  = 'http://localhost:8002';
const EXEC = 'http://localhost:8003';
const VAULT = 'http://localhost:8004';
const AGENT = 'http://localhost:8005';
const SEC   = 'http://localhost:8006';

// ── Types ──────────────────────────────────────────────────────────────────

export interface Project {
  id: string;
  name: string;
  repo_path: string;
  container_id: string | null;
  created_at: string;
  status: 'ready' | 'indexing' | 'error' | 'idle';
}

export interface Task {
  id: string;
  project_id: string;
  description: string;
  status: 'pending' | 'running' | 'complete' | 'failed';
  created_at: string;
  result?: string;
  diff?: string;
  token_usage?: { input: number; output: number; total: number };
}

export interface KGNode {
  id: string;
  type: 'File' | 'Function' | 'EnvRef' | 'Class';
  properties: Record<string, unknown>;
}

export interface KGEdge {
  from_id: string;
  to_id: string;
  edge_type: 'CALLS' | 'CONTAINS' | 'IMPORTS' | 'DEFINES';
}

export interface SecurityFinding {
  id: string;
  project_id: string;
  pattern: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  node_path: string[];
  description: string;
  suggested_fix: string;
  detected_at: string;
  resolved: boolean;
  task_id: string;
}

export interface LSMNode {
  node_id: string;
  node_type: string;
  snippet: string;
  proximity_score: number | null;
}

export interface BudgetPlan {
  tiers: Array<{
    name: string;
    nodes: LSMNode[];
    token_count: number;
    savings_pct: number;
  }>;
}

export interface VaultKey {
  key_name: string;
  project_id: string;
  created_at?: string;
}

// ── Helpers ────────────────────────────────────────────────────────────────

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} → ${res.status} ${res.statusText}`);
  return res.json();
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`POST ${url} → ${res.status} ${res.statusText}`);
  return res.json();
}

async function del<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'DELETE',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`DELETE ${url} → ${res.status} ${res.statusText}`);
  return res.json();
}

// ── Projects ───────────────────────────────────────────────────────────────

export const api = {
  projects: {
    list: () => get<{ projects: Project[] }>(`${ORCH}/api/v1/projects`),
    get: (id: string) => get<{ project: Project }>(`${ORCH}/api/v1/projects/${id}`),
    create: (name: string, repo_path: string) =>
      post<Project>(`${ORCH}/api/v1/projects`, { name, repo_path }),
    ingest: (id: string) =>
      post<{ job_id: string }>(`${ORCH}/api/v1/projects/${id}/ingest`),
  },

  // ── Tasks ────────────────────────────────────────────────────────────────
  tasks: {
    list: (project_id: string) =>
      get<Task[]>(`${ORCH}/api/v1/tasks?project_id=${project_id}`),
    get: (task_id: string) =>
      get<Task>(`${ORCH}/api/v1/tasks/${task_id}`),
    submit: (project_id: string, description: string, llm_api_key?: string) =>
      post<Task>(`${ORCH}/api/v1/tasks`, { project_id, description, llm_api_key }),
    getDiff: (task_id: string) =>
      get<{ diff: string }>(`${ORCH}/api/v1/tasks/${task_id}/diff`),
    apply: (task_id: string) =>
      post<Task>(`${ORCH}/api/v1/tasks/${task_id}/apply`),
    reject: (task_id: string) =>
      post<Task>(`${ORCH}/api/v1/tasks/${task_id}/reject`),
  },

  // ── Knowledge Graph ───────────────────────────────────────────────────────
  kg: {
    nodes: (project_id: string) =>
      get<KGNode[]>(`${KG}/kg/nodes?project_id=${project_id}`),
    edges: (project_id: string) =>
      get<KGEdge[]>(`${KG}/kg/edges?project_id=${project_id}`),
    subgraph: (project_id: string, node_id: string) =>
      get<{ nodes: KGNode[]; edges: KGEdge[] }>(
        `${KG}/kg/subgraph?project_id=${project_id}&node_id=${node_id}`
      ),
    node: (node_id: string) =>
      get<KGNode>(`${KG}/kg/node/${node_id}`),
    search: (project_id: string, query: string) =>
      get<KGNode[]>(`${KG}/kg/search?project_id=${project_id}&query=${encodeURIComponent(query)}`),
    callChain: (fn_id: string) =>
      get<{ chain: string[] }>(`${KG}/kg/call-chain/${fn_id}`),
    explore: (project_id: string) =>
      get<{ nodes: KGNode[]; edges: KGEdge[] }>(
        `${ORCH}/api/v1/kg/explore?project_id=${project_id}`
      ),
  },

  // ── LSM ───────────────────────────────────────────────────────────────────
  lsm: {
    proximity: (project_id: string, query: string, threshold = 0.7, top_k = 20) =>
      get<LSMNode[]>(
        `${LSM}/lsm/proximity?project_id=${project_id}&query=${encodeURIComponent(query)}&threshold=${threshold}&top_k=${top_k}`
      ),
    budgetPlan: (project_id: string, query: string) =>
      get<BudgetPlan>(
        `${LSM}/lsm/budget-plan?project_id=${project_id}&query=${encodeURIComponent(query)}`
      ),
  },

  // ── Security ──────────────────────────────────────────────────────────────
  security: {
    report: (project_id: string) =>
      get<{ project_id: string; findings: SecurityFinding[] }>(
        `${ORCH}/api/v1/security/report/${project_id}`
      ),
    scan: (project_id: string) =>
      post<{ scan_id: string }>(`${SEC}/security/scan`, { project_id }),
    history: (project_id: string) =>
      get<{ scans: unknown[] }>(`${SEC}/security/history/${project_id}`),
  },

  // ── Vault ─────────────────────────────────────────────────────────────────
  vault: {
    listKeys: (project_id: string) =>
      get<{ project_id: string; keys: VaultKey[] }>(
        `${VAULT}/vault/keys/${project_id}`
      ),
    store: (project_id: string, key_name: string, value: string) =>
      post<{ ok: boolean }>(`${VAULT}/vault/store`, { project_id, key_name, value }),
    inject: (project_id: string) =>
      post<{ ok: boolean }>(`${VAULT}/vault/inject/${project_id}`),
    deleteKey: (project_id: string, key_name: string) =>
      del<{ ok: boolean }>(`${VAULT}/vault/key`, { project_id, key_name }),
  },

  // ── Execution Env ─────────────────────────────────────────────────────────
  exec: {
    readFile: (project_id: string, path: string) =>
      get<{ content: string }>(
        `${EXEC}/exec/read-file?project_id=${project_id}&path=${encodeURIComponent(path)}`
      ),
    ls: (project_id: string, path = '/') =>
      get<{ entries: Array<{ name: string; type: 'file' | 'dir' }> }>(
        `${EXEC}/exec/ls?project_id=${project_id}&path=${encodeURIComponent(path)}`
      ),
    gitDiff: (project_id: string) =>
      get<{ diff: string }>(`${EXEC}/exec/git/diff?project_id=${project_id}`),
    containerStatus: (container_id: string) =>
      get<{ state: string; cpu: number; mem: number }>(
        `${EXEC}/exec/container/${container_id}/status`
      ),
  },

  // ── Health ────────────────────────────────────────────────────────────────
  health: {
    check: async (): Promise<boolean> => {
      try {
        await get(`${ORCH}/health`);
        return true;
      } catch {
        return false;
      }
    },
  },
};

// ── WebSocket ─────────────────────────────────────────────────────────────

export function createProjectWebSocket(
  project_id: string,
  onEvent: (event: { event: string; data: Record<string, unknown> }) => void,
  onClose?: () => void
): WebSocket {
  const ws = new WebSocket(`ws://localhost:8000/ws/${project_id}`);
  ws.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
    } catch {
      // ignore malformed messages
    }
  };
  ws.onclose = () => onClose?.();
  return ws;
}
