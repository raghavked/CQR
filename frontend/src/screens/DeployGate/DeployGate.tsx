/**
 * Deploy Gate — §7.7
 * Pre-deploy checklist: security, diff review, confirmation.
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore, useScanStore, useAgentStore } from '../../stores';
import { api } from '../../api/client';
import { Button, StatusChip, EmptyState, Panel, MetricReadout, ToastContainer, useToast } from '../../components';

interface CheckItem {
  id: string;
  label: string;
  status: 'pass' | 'fail' | 'warn' | 'pending';
  detail: string;
}

export const DeployGate: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const { findings } = useScanStore();
  const { tasks, activeTaskId } = useAgentStore();
  const { toasts, add: addToast, dismiss } = useToast();
  const [diff, setDiff] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const activeTask = tasks.find((t) => t.id === activeTaskId) || null;

  const loadDiff = useCallback(async () => {
    if (!activeTaskId) return;
    try {
      const data = await api.tasks.getDiff(activeTaskId);
      setDiff(data.diff);
    } catch {
      setDiff(null);
    }
  }, [activeTaskId]);

  useEffect(() => { loadDiff(); }, [loadDiff]);

  const criticals = findings.filter((f) => f.severity === 'critical' && !f.resolved);
  const highs = findings.filter((f) => f.severity === 'high' && !f.resolved);

  const checks: CheckItem[] = [
    {
      id: 'security',
      label: 'No critical vulnerabilities',
      status: criticals.length === 0 ? 'pass' : 'fail',
      detail: criticals.length === 0
        ? 'No critical findings detected'
        : `${criticals.length} critical finding${criticals.length > 1 ? 's' : ''} unresolved`,
    },
    {
      id: 'high',
      label: 'No high-severity findings',
      status: highs.length === 0 ? 'pass' : 'warn',
      detail: highs.length === 0
        ? 'No high-severity findings'
        : `${highs.length} high-severity finding${highs.length > 1 ? 's' : ''} — review recommended`,
    },
    {
      id: 'diff',
      label: 'Diff reviewed',
      status: diff !== null ? 'pass' : 'pending',
      detail: diff !== null ? 'Diff loaded and available for review' : 'No pending diff',
    },
    {
      id: 'task',
      label: 'Task complete',
      status: activeTask?.status === 'complete' ? 'pass' : activeTask?.status === 'failed' ? 'fail' : 'pending',
      detail: activeTask ? `Task status: ${activeTask.status}` : 'No active task',
    },
  ];

  const canDeploy = checks.every((c) => c.status === 'pass' || c.status === 'warn');
  const blockingFails = checks.filter((c) => c.status === 'fail');

  const handleApply = async () => {
    if (!activeTaskId) return;
    setApplying(true);
    try {
      await api.tasks.apply(activeTaskId);
      addToast('Changes applied successfully', 'success');
      setConfirmOpen(false);
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setApplying(false);
    }
  };

  const handleReject = async () => {
    if (!activeTaskId) return;
    setRejecting(true);
    try {
      await api.tasks.reject(activeTaskId);
      addToast('Changes rejected', 'info');
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setRejecting(false);
    }
  };

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to use the Deploy Gate." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: checklist */}
      <div style={{
        width: 320, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      }}>
        <div style={{ padding: 'var(--sp-3) var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span style={{ font: 'var(--type-title)', fontSize: 15 }}>Deploy Gate</span>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          {checks.map((c) => (
            <div key={c.id} style={{
              display: 'flex', gap: 'var(--sp-3)', alignItems: 'flex-start',
              padding: 'var(--sp-3)', background: 'var(--raised)', border: 'var(--border)'
            }}>
              <div style={{ flexShrink: 0, marginTop: 2 }}>
                {c.status === 'pass'    && <span style={{ color: 'var(--status-success)' }}>✓</span>}
                {c.status === 'fail'    && <span style={{ color: 'var(--status-danger)' }}>✗</span>}
                {c.status === 'warn'    && <span style={{ color: 'var(--status-warning)' }}>⚠</span>}
                {c.status === 'pending' && <span style={{ color: 'var(--text-muted)' }}>○</span>}
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 2 }}>{c.label}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{c.detail}</div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ padding: 'var(--sp-4)', borderTop: 'var(--border)', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
          {blockingFails.length > 0 && (
            <div style={{ fontSize: 12, color: 'var(--status-danger)', fontFamily: 'var(--font-mono)' }}>
              {blockingFails.length} check{blockingFails.length > 1 ? 's' : ''} blocking deploy
            </div>
          )}
          <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
            <Button
              variant="danger"
              onClick={handleReject}
              loading={rejecting}
              disabled={!activeTaskId}
              style={{ flex: 1 }}
            >
              Reject
            </Button>
            <Button
              variant="deploy"
              onClick={() => setConfirmOpen(true)}
              disabled={!canDeploy || !activeTaskId}
              style={{ flex: 1 }}
            >
              Apply Changes
            </Button>
          </div>
        </div>
      </div>

      {/* Right: diff view */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Diff Preview</span>
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-3)' }}>
          {diff ? (
            <pre style={{
              fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.6,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              color: 'var(--text-muted)',
            }}>
              {diff.split('\n').map((line, i) => (
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
              title="No diff available"
              description="Submit a task in the IDE to generate a diff for review."
              cta={{ label: 'Go to IDE', onClick: () => navigate('/ide') }}
            />
          )}
        </div>
      </div>

      {/* Confirm modal */}
      {confirmOpen && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 900
          }}
          onClick={() => setConfirmOpen(false)}
        >
          <div
            style={{
              background: 'var(--raised)', border: '1px solid var(--status-warning)',
              padding: 'var(--sp-5)', width: 400, display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)'
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ font: 'var(--type-title)' }}>⚠ Confirm Apply</div>
            <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              This will apply the agent's diff to the repository. This action cannot be undone without a git revert.
              {highs.length > 0 && (
                <div style={{ marginTop: 8, color: 'var(--status-warning)' }}>
                  {highs.length} high-severity finding{highs.length > 1 ? 's' : ''} detected — proceed with caution.
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
              <Button variant="ghost" onClick={() => setConfirmOpen(false)}>Cancel</Button>
              <Button variant="deploy" onClick={handleApply} loading={applying}>Apply Changes</Button>
            </div>
          </div>
        </div>
      )}

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
