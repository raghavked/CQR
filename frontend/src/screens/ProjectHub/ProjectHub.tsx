/**
 * Project Hub — §7.1
 * Landing surface: switch between projects/sessions, create new ones.
 */

import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Project } from '../../api/client';
import { useSessionStore } from '../../stores';
import {
  Card, StatusChip, MetricReadout, EmptyState, SkeletonCard, Button, useToast, ToastContainer
} from '../../components';
import type { Severity } from '../../components';

function projectSeverity(status: Project['status']): Severity {
  switch (status) {
    case 'ready':    return 'success';
    case 'indexing': return 'info';
    case 'error':    return 'danger';
    default:         return 'neutral';
  }
}

export const ProjectHub: React.FC = () => {
  const navigate = useNavigate();
  const { setActiveProject } = useSessionStore();
  const { toasts, add: addToast, dismiss } = useToast();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.projects.list();
      setProjects(data);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openProject = (p: Project) => {
    setActiveProject(p);
    navigate('/ide');
  };

  return (
    <div className="screen" style={{ overflow: 'auto' }}>
      <div className="screen__header">
        <h1 className="screen__title">Project Hub</h1>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--sp-2)' }}>
          <Button variant="ghost" onClick={load}>Refresh</Button>
          <Button variant="primary" onClick={() => navigate('/onboarding')}>+ New Project</Button>
        </div>
      </div>

      <div className="screen__body">
        {error && (
          <div style={{ color: 'var(--status-danger)', marginBottom: 'var(--sp-4)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
            {error} <Button variant="ghost" onClick={load} style={{ marginLeft: 8 }}>Retry</Button>
          </div>
        )}

        {loading ? (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 'var(--sp-4)' }}>
            {[1, 2, 3].map((i) => <SkeletonCard key={i} />)}
          </div>
        ) : projects.length === 0 ? (
          <EmptyState
            title="No projects yet"
            description="Connect your first repository to get started."
            cta={{ label: 'Connect Repository', onClick: () => navigate('/onboarding') }}
          />
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
            gap: 'var(--sp-4)'
          }}>
            {projects.map((p) => (
              <Card key={p.id} onClick={() => openProject(p)}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 'var(--sp-3)' }}>
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>{p.name}</div>
                    <div className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>
                      {p.id.slice(0, 8)}…
                    </div>
                  </div>
                  <StatusChip label={p.status} severity={projectSeverity(p.status)} />
                </div>
                <div style={{ display: 'flex', gap: 'var(--sp-4)', marginTop: 'var(--sp-3)' }}>
                  <MetricReadout
                    label="Repo"
                    value={p.repo_path.split('/').pop() || p.repo_path}
                  />
                  <MetricReadout
                    label="Created"
                    value={new Date(p.created_at).toLocaleDateString()}
                  />
                </div>
              </Card>
            ))}

            {/* New project tile */}
            <Card onClick={() => navigate('/onboarding')} style={{ border: '1px dashed var(--line)', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 120 }}>
              <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>+ New Project</span>
            </Card>
          </div>
        )}
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
