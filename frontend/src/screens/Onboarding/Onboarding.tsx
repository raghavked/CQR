/**
 * Onboarding — §7.2
 * One-time per project: Connect repo → Index codebase → Ready.
 */

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import { useSessionStore } from '../../stores';
import { Button, ProgressBar, StatusChip, ToastContainer, useToast } from '../../components';
import { clsx } from 'clsx';

type Step = 'connect' | 'index' | 'ready';

const SaturnMark: React.FC = () => (
  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" style={{ marginBottom: 'var(--sp-4)' }}>
    <circle cx="12" cy="12" r="4" fill="var(--text)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="1" fill="none" transform="rotate(-20 12 12)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="0.7" fill="none" opacity="0.5" transform="rotate(-30 12 12)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="0.5" fill="none" opacity="0.3" transform="rotate(-10 12 12)" />
  </svg>
);

export const Onboarding: React.FC = () => {
  const navigate = useNavigate();
  const { setActiveProject } = useSessionStore();
  const { toasts, add: addToast, dismiss } = useToast();

  const [step, setStep] = useState<Step>('connect');
  const [projectName, setProjectName] = useState('');
  const [repoPath, setRepoPath] = useState('');
  const [indexProgress, setIndexProgress] = useState(0);
  const [indexMessage, setIndexMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdProject, setCreatedProject] = useState<{ id: string; name: string } | null>(null);

  const handleConnect = async () => {
    if (!projectName.trim() || !repoPath.trim()) {
      setError('Project name and repository path are required.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const project = await api.projects.create(projectName.trim(), repoPath.trim());
      setCreatedProject(project);
      setStep('index');
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleIndex = async () => {
    if (!createdProject) return;
    setLoading(true);
    setError(null);
    setIndexProgress(0);
    setIndexMessage('Starting indexing…');

    try {
      await api.projects.ingest(createdProject.id);

      // Simulate progress since backend doesn't emit per-file SSE (confirmed: start/done only)
      // Show honest indeterminate progress with polling
      let progress = 0;
      const interval = setInterval(() => {
        progress = Math.min(progress + Math.random() * 8, 90);
        setIndexProgress(Math.round(progress));
        setIndexMessage(`Parsing files… (${Math.round(progress)}%)`);
      }, 400);

      // Poll for completion
      let attempts = 0;
      while (attempts < 60) {
        await new Promise((r) => setTimeout(r, 2000));
        attempts++;
        try {
          const detail = await api.projects.get(createdProject.id);
          if (detail.project.status === 'ready') {
            clearInterval(interval);
            setIndexProgress(100);
            setIndexMessage('Indexing complete!');
            setActiveProject(detail.project);
            setStep('ready');
            break;
          } else if (detail.project.status === 'error') {
            clearInterval(interval);
            setError('Indexing encountered errors. You may continue with a partial graph.');
            setStep('ready');
            break;
          }
        } catch {
          // continue polling
        }
      }
      clearInterval(interval);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const steps: { id: Step; label: string; num: number }[] = [
    { id: 'connect', label: 'Connect Repository', num: 1 },
    { id: 'index',   label: 'Index Codebase',     num: 2 },
    { id: 'ready',   label: 'Ready',               num: 3 },
  ];

  return (
    <div className="screen" style={{ alignItems: 'center', justifyContent: 'center', overflow: 'auto' }}>
      <div style={{ width: '100%', maxWidth: 560, padding: 'var(--sp-7) var(--sp-5)' }}>
        {/* Saturn mark */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 'var(--sp-6)' }}>
          <SaturnMark />
          <h1 style={{ font: 'var(--type-title)', fontSize: 18 }}>CQR Setup</h1>
        </div>

        {/* Stepper */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 0, marginBottom: 'var(--sp-6)' }}>
          {steps.map((s, i) => (
            <React.Fragment key={s.id}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                <div style={{
                  width: 28, height: 28,
                  border: `1px solid ${step === s.id ? 'var(--status-info)' : steps.findIndex(x => x.id === step) > i ? 'var(--status-success)' : 'var(--line)'}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: 12,
                  color: step === s.id ? 'var(--status-info)' : steps.findIndex(x => x.id === step) > i ? 'var(--status-success)' : 'var(--text-muted)',
                }}>
                  {steps.findIndex(x => x.id === step) > i ? '✓' : s.num}
                </div>
                <span style={{ fontSize: 11, color: step === s.id ? 'var(--text)' : 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                  {s.label}
                </span>
              </div>
              {i < steps.length - 1 && (
                <div style={{ flex: 1, height: 1, background: 'var(--line)', margin: '0 var(--sp-2)', marginBottom: 20 }} />
              )}
            </React.Fragment>
          ))}
        </div>

        {/* Step content */}
        <div style={{ background: 'var(--surface)', border: 'var(--border)', padding: 'var(--sp-5)' }}>
          {step === 'connect' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
              <h2 style={{ font: 'var(--type-title)' }}>Connect Repository</h2>
              <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                Enter the local path to your repository. CQR will parse and index it into the Knowledge Graph.
              </p>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
                <label className="label" htmlFor="project-name">Project Name</label>
                <input
                  id="project-name"
                  type="text"
                  value={projectName}
                  onChange={(e) => setProjectName(e.target.value)}
                  placeholder="my-project"
                  style={{
                    background: 'var(--raised)', border: 'var(--border)', color: 'var(--text)',
                    padding: 'var(--sp-2) var(--sp-3)', fontFamily: 'var(--font-mono)', fontSize: 13,
                    outline: 'none', width: '100%'
                  }}
                  className="selectable"
                />
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-2)' }}>
                <label className="label" htmlFor="repo-path">Repository Path</label>
                <input
                  id="repo-path"
                  type="text"
                  value={repoPath}
                  onChange={(e) => setRepoPath(e.target.value)}
                  placeholder="/path/to/your/repo"
                  style={{
                    background: 'var(--raised)', border: 'var(--border)', color: 'var(--text)',
                    padding: 'var(--sp-2) var(--sp-3)', fontFamily: 'var(--font-mono)', fontSize: 13,
                    outline: 'none', width: '100%'
                  }}
                  className="selectable"
                />
              </div>

              {error && <div style={{ color: 'var(--status-danger)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>{error}</div>}

              <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end', marginTop: 'var(--sp-2)' }}>
                <Button variant="ghost" onClick={() => navigate('/')}>Cancel</Button>
                <Button variant="primary" onClick={handleConnect} loading={loading}>
                  Connect Repository →
                </Button>
              </div>
            </div>
          )}

          {step === 'index' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)' }}>
              <h2 style={{ font: 'var(--type-title)' }}>Index Codebase</h2>
              <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                CQR will parse your repository and build the Knowledge Graph. This may take a few minutes for large codebases.
              </p>

              {loading ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
                  <ProgressBar value={indexProgress} label={indexMessage} />
                  <div className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {indexMessage}
                  </div>
                </div>
              ) : (
                <>
                  {error && (
                    <div style={{ color: 'var(--status-warning)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
                      {error}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: 'var(--sp-2)', justifyContent: 'flex-end' }}>
                    <Button variant="primary" onClick={handleIndex} loading={loading}>
                      Start Indexing →
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {step === 'ready' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-4)', alignItems: 'center', textAlign: 'center' }}>
              <div style={{ fontSize: 32 }}>✓</div>
              <h2 style={{ font: 'var(--type-title)', color: 'var(--status-success)' }}>Ready</h2>
              <p style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                Your repository has been indexed. The Knowledge Graph is ready.
              </p>
              <Button variant="deploy" onClick={() => navigate('/ide')}>
                Open IDE →
              </Button>
            </div>
          )}
        </div>
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
