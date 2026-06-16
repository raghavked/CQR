/**
 * Vault — §7.8
 * Secret management: list keys, add new, inject into container, delete.
 * Values never shown in renderer (write-only).
 */

import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useSessionStore, useVaultStore } from '../../stores';
import { api } from '../../api/client';
import { Button, EmptyState, StatusChip, Panel, ToastContainer, useToast, Modal } from '../../components';

export const Vault: React.FC = () => {
  const navigate = useNavigate();
  const { activeProject } = useSessionStore();
  const { keys, loading, setKeys, setLoading, setError } = useVaultStore();
  const { toasts, add: addToast, dismiss } = useToast();

  const [addOpen, setAddOpen] = useState(false);
  const [keyName, setKeyName] = useState('');
  const [keyValue, setKeyValue] = useState('');
  const [saving, setSaving] = useState(false);
  const [injecting, setInjecting] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const loadKeys = useCallback(async () => {
    if (!activeProject) return;
    setLoading(true);
    try {
      const data = await api.vault.listKeys(activeProject.id);
      setKeys(data.keys);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [activeProject, setKeys, setLoading, setError]);

  useEffect(() => { loadKeys(); }, [loadKeys]);

  const handleAdd = async () => {
    if (!activeProject || !keyName.trim() || !keyValue.trim()) return;
    setSaving(true);
    try {
      await api.vault.store(activeProject.id, keyName.trim(), keyValue.trim());
      addToast(`Secret "${keyName}" stored`, 'success');
      setKeyName('');
      setKeyValue('');
      setAddOpen(false);
      await loadKeys();
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleInject = async () => {
    if (!activeProject) return;
    setInjecting(true);
    try {
      await api.vault.inject(activeProject.id);
      addToast('Secrets injected into container', 'success');
    } catch (e) {
      addToast(String(e), 'error');
    } finally {
      setInjecting(false);
    }
  };

  const handleDelete = async (keyName: string) => {
    if (!activeProject) return;
    try {
      await api.vault.deleteKey(activeProject.id, keyName);
      addToast(`Secret "${keyName}" deleted`, 'info');
      setDeleteTarget(null);
      await loadKeys();
    } catch (e) {
      addToast(String(e), 'error');
    }
  };

  if (!activeProject) {
    return <EmptyState title="No project open" description="Open a project to manage secrets." cta={{ label: 'Project Hub', onClick: () => navigate('/') }} />;
  }

  return (
    <div className="screen" style={{ overflow: 'auto' }}>
      <div className="screen__header">
        <h1 className="screen__title">Vault</h1>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--sp-2)' }}>
          <Button variant="ghost" onClick={handleInject} loading={injecting}>
            Inject into Container
          </Button>
          <Button variant="primary" onClick={() => setAddOpen(true)}>
            + Add Secret
          </Button>
        </div>
      </div>

      <div className="screen__body">
        {/* Security notice */}
        <div style={{
          padding: 'var(--sp-3) var(--sp-4)',
          background: 'var(--raised)', border: 'var(--border)',
          borderLeft: '3px solid var(--status-warning)',
          marginBottom: 'var(--sp-4)', fontSize: 12, color: 'var(--text-muted)'
        }}>
          <strong style={{ color: 'var(--status-warning)' }}>Security Notice:</strong> Secret values are write-only and never displayed. Keys are stored encrypted and injected into the sandbox container at runtime.
        </div>

        {loading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>Loading…</div>
        ) : keys.length === 0 ? (
          <EmptyState
            title="No secrets stored"
            description="Add API keys and tokens to inject them securely into the agent sandbox."
            cta={{ label: 'Add Secret', onClick: () => setAddOpen(true) }}
          />
        ) : (
          <div style={{ border: 'var(--border)' }}>
            {/* Header row */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 160px 80px',
              padding: 'var(--sp-2) var(--sp-4)',
              background: 'var(--raised)', borderBottom: 'var(--border)',
              fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.04em'
            }}>
              <span>Key Name</span>
              <span>Created</span>
              <span></span>
            </div>

            {keys.map((k) => (
              <div
                key={k.key_name}
                style={{
                  display: 'grid', gridTemplateColumns: '1fr 160px 80px',
                  padding: 'var(--sp-3) var(--sp-4)',
                  borderBottom: 'var(--border)',
                  alignItems: 'center',
                }}
              >
                <span className="mono" style={{ fontSize: 13 }}>{k.key_name}</span>
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  {k.created_at ? new Date(k.created_at).toLocaleDateString() : '—'}
                </span>
                <Button
                  variant="danger"
                  onClick={() => setDeleteTarget(k.key_name)}
                  style={{ fontSize: 11 }}
                >
                  Delete
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add secret modal */}
      <Modal
        open={addOpen}
        onClose={() => { setAddOpen(false); setKeyName(''); setKeyValue(''); }}
        title="Add Secret"
        actions={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button variant="primary" onClick={handleAdd} loading={saving} disabled={!keyName.trim() || !keyValue.trim()}>
              Store Secret
            </Button>
          </>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
          <div>
            <label className="label" htmlFor="vault-key-name" style={{ display: 'block', marginBottom: 4 }}>Key Name</label>
            <input
              id="vault-key-name"
              type="text"
              value={keyName}
              onChange={(e) => setKeyName(e.target.value)}
              placeholder="ANTHROPIC_API_KEY"
              style={{
                width: '100%', background: 'var(--raised)', border: 'var(--border)',
                color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 13,
                padding: 'var(--sp-2)', outline: 'none'
              }}
              className="selectable"
              autoComplete="off"
            />
          </div>
          <div>
            <label className="label" htmlFor="vault-key-value" style={{ display: 'block', marginBottom: 4 }}>
              Value <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(write-only)</span>
            </label>
            <input
              id="vault-key-value"
              type="password"
              value={keyValue}
              onChange={(e) => setKeyValue(e.target.value)}
              placeholder="sk-ant-api03-…"
              style={{
                width: '100%', background: 'var(--raised)', border: 'var(--border)',
                color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 13,
                padding: 'var(--sp-2)', outline: 'none'
              }}
              className="selectable"
              autoComplete="new-password"
            />
          </div>
        </div>
      </Modal>

      {/* Delete confirm modal */}
      <Modal
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Delete Secret"
        variant="destructive"
        actions={
          <>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button variant="danger" onClick={() => deleteTarget && handleDelete(deleteTarget)}>
              Delete "{deleteTarget}"
            </Button>
          </>
        }
      >
        <p style={{ fontSize: 13, color: 'var(--text-muted)' }}>
          This will permanently delete the secret <strong style={{ color: 'var(--text)' }}>{deleteTarget}</strong>. This action cannot be undone.
        </p>
      </Modal>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
