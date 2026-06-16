/**
 * IDE Main — §7.3
 * Flagship working surface. Agent works here; human observes.
 * 3-column: file tree | Monaco editor | Agent activity panel
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import Editor from '@monaco-editor/react';
import { useSessionStore, useAgentStore } from '../../stores';
import { api, createProjectWebSocket } from '../../api/client';
import { Button, StatusChip, EmptyState, Spinner, ToastContainer, useToast } from '../../components';

// ── File Tree ──────────────────────────────────────────────────────────────

interface FileEntry { name: string; type: 'file' | 'dir'; path: string; }

const FileTree: React.FC<{
  projectId: string;
  onSelect: (path: string) => void;
  selectedPath: string | null;
}> = ({ projectId, onSelect, selectedPath }) => {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.exec.ls(projectId).then((res) => {
      setEntries(res.entries.map((e) => ({ ...e, path: e.name })));
    }).catch(() => {}).finally(() => setLoading(false));
  }, [projectId]);

  if (loading) return <div style={{ padding: 'var(--sp-3)' }}><Spinner size={14} /></div>;

  return (
    <div style={{ overflow: 'auto', flex: 1 }}>
      {entries.map((e) => (
        <div
          key={e.path}
          onClick={() => e.type === 'file' && onSelect(e.path)}
          style={{
            padding: '3px var(--sp-3)',
            cursor: e.type === 'file' ? 'pointer' : 'default',
            fontFamily: 'var(--font-mono)',
            fontSize: 12,
            color: selectedPath === e.path ? 'var(--text)' : 'var(--text-muted)',
            background: selectedPath === e.path ? 'var(--raised)' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            whiteSpace: 'nowrap',
          }}
          role={e.type === 'file' ? 'button' : undefined}
          tabIndex={e.type === 'file' ? 0 : undefined}
          onKeyDown={(ev) => ev.key === 'Enter' && e.type === 'file' && onSelect(e.path)}
        >
          <span style={{ opacity: 0.5 }}>{e.type === 'dir' ? '▶' : '·'}</span>
          {e.name}
        </div>
      ))}
    </div>
  );
};

// ── Agent Activity Panel ───────────────────────────────────────────────────

const AgentActivityPanel: React.FC<{
  projectId: string;
  onSubmitTask: (desc: string, key: string) => void;
  loading: boolean;
}> = ({ projectId, onSubmitTask, loading }) => {
  const { activityLog, addActivity } = useAgentStore();
  const { agentState } = useSessionStore();
  const [taskDesc, setTaskDesc] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showKeyField, setShowKeyField] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [activityLog]);

  const handleSubmit = () => {
    if (!taskDesc.trim()) return;
    onSubmitTask(taskDesc.trim(), apiKey);
    setTaskDesc('');
    setApiKey('');
    setShowKeyField(false);
  };

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      borderLeft: 'var(--border)', background: 'var(--surface)'
    }}>
      {/* Header */}
      <div style={{
        padding: 'var(--sp-2) var(--sp-3)',
        borderBottom: 'var(--border)',
        display: 'flex', alignItems: 'center', gap: 'var(--sp-2)',
        flexShrink: 0
      }}>
        <span className="label">Agent Activity</span>
        <StatusChip
          label={agentState}
          severity={agentState === 'working' ? 'info' : agentState === 'blocked' ? 'warning' : 'neutral'}
        />
      </div>

      {/* Activity log */}
      <div
        style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-2)' }}
        role="log"
        aria-live="polite"
        aria-label="Agent activity log"
      >
        {activityLog.length === 0 ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 12, fontFamily: 'var(--font-mono)', padding: 'var(--sp-2)' }}>
            Ready. Submit a task below.
          </div>
        ) : (
          activityLog.map((entry, i) => (
            <div key={i} style={{
              fontFamily: 'var(--font-mono)', fontSize: 11,
              color: entry.type === 'error' ? 'var(--status-danger)' :
                     entry.type === 'success' ? 'var(--status-success)' : 'var(--text-muted)',
              padding: '2px 0',
              borderBottom: '1px solid var(--bg-base)',
            }}>
              <span style={{ opacity: 0.5 }}>{new Date(entry.ts).toLocaleTimeString()} </span>
              {entry.message}
            </div>
          ))
        )}
        <div ref={logEndRef} />
      </div>

      {/* Task input */}
      <div style={{ padding: 'var(--sp-3)', borderTop: 'var(--border)', flexShrink: 0 }}>
        <textarea
          value={taskDesc}
          onChange={(e) => setTaskDesc(e.target.value)}
          placeholder="Describe the task for the agent…"
          rows={3}
          style={{
            width: '100%', background: 'var(--raised)', border: 'var(--border)',
            color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
            padding: 'var(--sp-2)', resize: 'none', outline: 'none',
            marginBottom: 'var(--sp-2)'
          }}
          className="selectable"
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit();
          }}
        />

        {showKeyField && (
          <div style={{ marginBottom: 'var(--sp-2)' }}>
            <div className="label" style={{ marginBottom: 4 }}>
              LLM API Key — <span style={{ color: 'var(--status-warning)', fontWeight: 400 }}>used for this run only, then discarded</span>
            </div>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-api03-…"
              style={{
                width: '100%', background: 'var(--raised)', border: 'var(--border)',
                color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
                padding: 'var(--sp-2)', outline: 'none'
              }}
              className="selectable"
              autoComplete="off"
            />
          </div>
        )}

        <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
          <Button
            variant="ghost"
            onClick={() => setShowKeyField((v) => !v)}
            style={{ fontSize: 11 }}
          >
            {showKeyField ? 'Hide key' : 'Add API key'}
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            loading={loading}
            style={{ flex: 1 }}
            disabled={!taskDesc.trim()}
          >
            Run Task
          </Button>
        </div>
      </div>
    </div>
  );
};

// ── IDE Main ───────────────────────────────────────────────────────────────

export const IDEMain: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject, setAgentState } = useSessionStore();
  const { addActivity, setActiveTask } = useAgentStore();
  const { toasts, add: addToast, dismiss } = useToast();
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState('');
  const [taskLoading, setTaskLoading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Connect WebSocket for project events
  useEffect(() => {
    if (!activeProject) return;
    const ws = createProjectWebSocket(
      activeProject.id,
      (event) => {
        const { event: type, data } = event as { event: string; data: Record<string, unknown> };
        switch (type) {
          case 'task.started':
            setAgentState('working');
            addActivity('info', `Task started — budget: ${data.budget_tier}`);
            break;
          case 'task.context_assembled':
            addActivity('info', `Context assembled — ${data.token_count} tokens, ${data.node_count} nodes`);
            break;
          case 'task.streaming':
            addActivity('info', String(data.delta || ''));
            break;
          case 'task.diff_ready':
            addActivity('success', `Diff ready — confidence: ${data.confidence}`);
            break;
          case 'task.applied':
            setAgentState('idle');
            addActivity('success', `Applied — ${data.files_changed} files changed`);
            setTaskLoading(false);
            break;
          case 'task.failed':
            setAgentState('idle');
            addActivity('error', `Failed: ${data.error}`);
            setTaskLoading(false);
            break;
          case 'security.alert':
            addActivity('error', `Security alert: ${data.severity} — ${data.path}`);
            break;
        }
      },
      () => addActivity('error', 'WebSocket disconnected')
    );
    wsRef.current = ws;
    return () => ws.close();
  }, [activeProject, addActivity, setAgentState]);

  // Load file content when selected
  useEffect(() => {
    if (!selectedFile || !activeProject) return;
    api.exec.readFile(activeProject.id, selectedFile)
      .then((res) => setFileContent(res.content))
      .catch(() => setFileContent('// Could not load file'));
  }, [selectedFile, activeProject]);

  const handleSubmitTask = useCallback(async (description: string, llmApiKey: string) => {
    if (!activeProject) return;
    setTaskLoading(true);
    try {
      const task = await api.tasks.submit(activeProject.id, description, llmApiKey || undefined);
      setActiveTask(task.id);
      addActivity('info', `Task submitted: ${description.slice(0, 60)}…`);
    } catch (e) {
      addActivity('error', String(e));
      setTaskLoading(false);
    }
  }, [activeProject, addActivity, setActiveTask]);

  if (!activeProject) {
    return (
      <EmptyState
        title="No project open"
        description="Open a project from the Project Hub to use the IDE."
        cta={{ label: 'Go to Project Hub', onClick: () => navigate('/') }}
      />
    );
  }

  const fileExt = selectedFile?.split('.').pop() || 'python';
  const monacoLang = fileExt === 'py' ? 'python' : fileExt === 'ts' ? 'typescript' : fileExt === 'js' ? 'javascript' : fileExt === 'go' ? 'go' : 'plaintext';

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* File tree */}
      <div style={{
        width: 200, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Files</span>
        </div>
        <FileTree
          projectId={activeProject.id}
          onSelect={setSelectedFile}
          selectedPath={selectedFile}
        />
      </div>

      {/* Monaco editor */}
      <div style={{ flex: 1, overflow: 'hidden', position: 'relative' }}>
        {selectedFile ? (
          <Editor
            height="100%"
            language={monacoLang}
            value={fileContent}
            theme="vs-dark"
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 13,
              fontFamily: 'JetBrains Mono, monospace',
              lineNumbers: 'on',
              scrollBeyondLastLine: false,
              wordWrap: 'off',
              renderLineHighlight: 'line',
              cursorStyle: 'line',
              padding: { top: 8 },
            }}
          />
        ) : (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: 'var(--text-muted)', fontFamily: 'var(--font-mono)', fontSize: 13
          }}>
            Select a file to view
          </div>
        )}
      </div>

      {/* Agent activity panel */}
      <div style={{ width: 300, flexShrink: 0 }}>
        <AgentActivityPanel
          projectId={activeProject.id}
          onSubmitTask={handleSubmitTask}
          loading={taskLoading}
        />
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
