/**
 * Sandbox — §7.9
 * Container status, resource meters, git diff, file browser.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore } from '../../stores';
import { api } from '../../api/client';
import { Button, EmptyState, StatusChip, MetricReadout, ProgressBar, Panel } from '../../components';

interface ContainerStatus {
  state: string;
  cpu: number;
  mem: number;
}

export const Sandbox: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const [status, setStatus] = useState<ContainerStatus | null>(null);
  const [gitDiff, setGitDiff] = useState<string | null>(null);
  const [files, setFiles] = useState<Array<{ name: string; type: 'file' | 'dir' }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!activeProject) return;
    setLoading(true);
    setError(null);
    try {
      const [diffData, lsData] = await Promise.all([
        api.exec.gitDiff(activeProject.id).catch(() => ({ diff: '' })),
        api.exec.ls(activeProject.id).catch(() => ({ entries: [] })),
      ]);
      setGitDiff(diffData.diff || null);
      setFiles(lsData.entries);

      if (activeProject.container_id) {
        const s = await api.exec.containerStatus(activeProject.container_id).catch(() => null);
        setStatus(s);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [activeProject]);

  useEffect(() => { load(); }, [load]);

  // Poll status every 5s
  useEffect(() => {
    if (!activeProject?.container_id) return;
    const interval = setInterval(async () => {
      try {
        const s = await api.exec.containerStatus(activeProject.container_id!);
        setStatus(s);
      } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [activeProject]);

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to view its sandbox." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: container info + file tree */}
      <div style={{
        width: 260, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      }}>
        <div style={{ padding: 'var(--sp-3) var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Sandbox</span>
        </div>

        {/* Container status */}
        <div style={{ padding: 'var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', marginBottom: 'var(--sp-3)' }}>
            <span className="label">Container</span>
            {status && (
              <StatusChip
                label={status.state}
                severity={status.state === 'running' ? 'success' : status.state === 'stopped' ? 'neutral' : 'warning'}
              />
            )}
          </div>

          {status ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>CPU</div>
                <ProgressBar value={Math.round(status.cpu * 100)} label={`${(status.cpu * 100).toFixed(1)}%`} />
              </div>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Memory</div>
                <ProgressBar value={Math.round(status.mem * 100)} label={`${(status.mem * 100).toFixed(1)}%`} />
              </div>
            </div>
          ) : (
            <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              {activeProject.container_id ? 'Loading…' : 'No container attached'}
            </div>
          )}
        </div>

        {/* File tree */}
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Files</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {files.map((f) => (
            <div
              key={f.name}
              style={{
                padding: '3px var(--sp-3)',
                fontFamily: 'var(--font-mono)', fontSize: 12,
                color: 'var(--text-muted)',
                display: 'flex', alignItems: 'center', gap: 6,
              }}
            >
              <span style={{ opacity: 0.5 }}>{f.type === 'dir' ? '▶' : '·'}</span>
              {f.name}
            </div>
          ))}
        </div>

        <div style={{ padding: 'var(--sp-3)', borderTop: 'var(--border)', flexShrink: 0 }}>
          <Button variant="ghost" onClick={load} style={{ width: '100%', fontSize: 11 }}>Refresh</Button>
        </div>
      </div>

      {/* Right: git diff */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Git Working Tree Diff</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-3)' }}>
          {error ? (
            <div style={{ color: 'var(--status-danger)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>{error}</div>
          ) : gitDiff ? (
            <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {gitDiff.split('\n').map((line, i) => (
                <div
                  key={i}
                  style={{
                    color: line.startsWith('+') ? 'var(--status-success)' :
                           line.startsWith('-') ? 'var(--status-danger)' :
                           line.startsWith('@@') ? 'var(--status-info)' : 'var(--text-muted)',
                    background: line.startsWith('+') ? 'rgba(68,170,102,0.08)' :
                                line.startsWith('-') ? 'rgba(204,68,68,0.08)' : 'transparent',
                    paddingLeft: 4,
                  }}
                >
                  {line}
                </div>
              ))}
            </pre>
          ) : (
            <EmptyState
              title="Clean working tree"
              description="No uncommitted changes in the repository."
            />
          )}
        </div>
      </div>
    </div>
  );
};
