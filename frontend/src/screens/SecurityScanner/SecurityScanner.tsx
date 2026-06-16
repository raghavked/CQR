/**
 * Security Scanner — §7.6
 * Findings table + detail pane. Scan trigger. Severity breakdown.
 */

import React, { useEffect, useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore, useScanStore } from '../../stores';
import { api, type SecurityFinding } from '../../api/client';
import {
  Button, StatusChip, EmptyState, Spinner, Panel, MetricReadout, ToastContainer, useToast
} from '../../components';
import { clsx } from 'clsx';

type Severity = SecurityFinding['severity'];

function severityOrder(s: Severity): number {
  return { critical: 0, high: 1, medium: 2, low: 3, info: 4 }[s] ?? 5;
}

function severityToStatus(s: Severity): 'danger' | 'warning' | 'info' | 'neutral' {
  switch (s) {
    case 'critical': return 'danger';
    case 'high':     return 'danger';
    case 'medium':   return 'warning';
    case 'low':      return 'info';
    default:         return 'neutral';
  }
}

export const SecurityScanner: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const { findings, selectedFindingId, scanning, scanProgress, setFindings, setSelectedFinding, setScanning, setScanProgress, setError } = useScanStore();
  const { toasts, add: addToast, dismiss } = useToast();
  const [filterSeverity, setFilterSeverity] = useState<Severity | 'all'>('all');

  const loadReport = useCallback(async () => {
    if (!activeProject) return;
    try {
      const data = await api.security.report(activeProject.id);
      setFindings(data.findings);
    } catch (e) {
      setError(String(e));
    }
  }, [activeProject, setFindings, setError]);

  useEffect(() => { loadReport(); }, [loadReport]);

  const handleScan = async () => {
    if (!activeProject) return;
    setScanning(true);
    setScanProgress(0);
    try {
      await api.security.scan(activeProject.id);
      // Simulate progress
      let p = 0;
      const interval = setInterval(() => {
        p = Math.min(p + Math.random() * 15, 90);
        setScanProgress(Math.round(p));
      }, 300);
      await new Promise((r) => setTimeout(r, 3000));
      clearInterval(interval);
      setScanProgress(100);
      await loadReport();
      addToast('Scan complete', 'success');
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setScanning(false);
      setScanProgress(0);
    }
  };

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to run security scans." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  const sorted = findings.slice().sort((a, b) => severityOrder(a.severity) - severityOrder(b.severity));
  const filtered = filterSeverity === 'all' ? sorted : sorted.filter((f) => f.severity === filterSeverity);
  const selected = findings.find((f) => f.id === selectedFindingId) || null;

  const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  findings.forEach((f) => counts[f.severity]++);

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: findings list */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{
          padding: 'var(--sp-3) var(--sp-4)',
          borderBottom: 'var(--border)',
          display: 'flex', alignItems: 'center', gap: 'var(--sp-3)', flexShrink: 0
        }}>
          <span className="screen__title" style={{ fontSize: 15 }}>Security Scanner</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--sp-2)', alignItems: 'center' }}>
            {scanning && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)', fontSize: 12, color: 'var(--text-muted)' }}>
                <Spinner size={12} />
                Scanning… {scanProgress}%
              </div>
            )}
            <Button variant="primary" onClick={handleScan} loading={scanning}>
              Run Scan
            </Button>
          </div>
        </div>

        {/* Severity summary */}
        <div style={{
          display: 'flex', gap: 'var(--sp-4)', padding: 'var(--sp-3) var(--sp-4)',
          borderBottom: 'var(--border)', flexShrink: 0
        }}>
          {(['critical', 'high', 'medium', 'low', 'info'] as Severity[]).map((s) => (
            <div
              key={s}
              onClick={() => setFilterSeverity(filterSeverity === s ? 'all' : s)}
              style={{ cursor: 'pointer', opacity: filterSeverity !== 'all' && filterSeverity !== s ? 0.4 : 1 }}
            >
              <MetricReadout
                label={s}
                value={counts[s]}
              />
            </div>
          ))}
        </div>

        {/* Findings table */}
        {findings.length === 0 ? (
          <EmptyState
            title="No findings"
            description={scanning ? 'Scan in progress…' : 'Run a scan to detect vulnerabilities.'}
          />
        ) : (
          <div style={{ flex: 1, overflow: 'auto' }} role="list" aria-label="Security findings">
            {filtered.map((f) => (
              <div
                key={f.id}
                onClick={() => setSelectedFinding(f.id === selectedFindingId ? null : f.id)}
                style={{
                  padding: 'var(--sp-3) var(--sp-4)',
                  borderBottom: 'var(--border)',
                  cursor: 'pointer',
                  background: f.id === selectedFindingId ? 'var(--raised)' : 'transparent',
                  borderLeft: f.id === selectedFindingId ? '2px solid var(--status-info)' : '2px solid transparent',
                  display: 'flex', flexDirection: 'column', gap: 'var(--sp-1)',
                }}
                role="listitem"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setSelectedFinding(f.id)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-2)' }}>
                  <StatusChip label={f.severity} severity={severityToStatus(f.severity)} />
                  <span style={{ fontSize: 13, fontWeight: 500 }}>{f.pattern}</span>
                  {f.resolved && <StatusChip label="resolved" severity="success" />}
                </div>
                <div className="mono truncate" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {f.node_path.join(' → ')}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Right: detail pane */}
      <div style={{
        width: 320, flexShrink: 0, borderLeft: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'hidden'
      }}>
        <div style={{ padding: 'var(--sp-2) var(--sp-3)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span className="label">Finding Detail</span>
        </div>

        {selected ? (
          <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-4)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Severity</div>
              <StatusChip label={selected.severity} severity={severityToStatus(selected.severity)} />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Pattern</div>
              <div className="mono" style={{ fontSize: 12 }}>{selected.pattern}</div>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Description</div>
              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{selected.description}</div>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Call Path</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 2 }}>
                {selected.node_path.map((p, i) => (
                  <div key={i} style={{ paddingLeft: i * 8 }}>
                    {i > 0 ? '↳ ' : ''}{p}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Suggested Fix</div>
              <div style={{
                background: 'var(--raised)', border: 'var(--border)',
                padding: 'var(--sp-3)', fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.6
              }}>
                {selected.suggested_fix}
              </div>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Detected</div>
              <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {new Date(selected.detected_at).toLocaleString()}
              </div>
            </div>
            <Button variant="ghost" onClick={() => navigate('/ide')} style={{ fontSize: 11 }}>
              Open in IDE →
            </Button>
          </div>
        ) : (
          <div style={{ padding: 'var(--sp-4)', color: 'var(--text-muted)', fontSize: 12 }}>
            Select a finding to view details
          </div>
        )}
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
