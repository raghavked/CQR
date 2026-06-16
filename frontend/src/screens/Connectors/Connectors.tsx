/**
 * Connectors — §7.10
 * Enable/disable integrations: GitHub, Jira, Slack, CI/CD.
 */

import React, { useState } from 'react';
import { Toggle, Panel, StatusChip, Button, ToastContainer, useToast } from '../../components';

interface Connector {
  id: string;
  name: string;
  description: string;
  category: 'vcs' | 'pm' | 'comms' | 'cicd' | 'llm';
  enabled: boolean;
  status: 'connected' | 'disconnected' | 'error';
  configFields?: Array<{ key: string; label: string; type: 'text' | 'password' | 'url'; placeholder: string }>;
}

const DEFAULT_CONNECTORS: Connector[] = [
  {
    id: 'github',
    name: 'GitHub',
    description: 'Push diffs, create PRs, and sync repository state.',
    category: 'vcs',
    enabled: false,
    status: 'disconnected',
    configFields: [
      { key: 'token', label: 'Personal Access Token', type: 'password', placeholder: 'ghp_…' },
      { key: 'repo', label: 'Repository', type: 'text', placeholder: 'owner/repo' },
    ],
  },
  {
    id: 'jira',
    name: 'Jira',
    description: 'Link tasks to Jira tickets and update status automatically.',
    category: 'pm',
    enabled: false,
    status: 'disconnected',
    configFields: [
      { key: 'url', label: 'Jira URL', type: 'url', placeholder: 'https://yourorg.atlassian.net' },
      { key: 'email', label: 'Email', type: 'text', placeholder: 'you@yourorg.com' },
      { key: 'token', label: 'API Token', type: 'password', placeholder: 'ATATT3x…' },
    ],
  },
  {
    id: 'slack',
    name: 'Slack',
    description: 'Post agent activity and security alerts to a Slack channel.',
    category: 'comms',
    enabled: false,
    status: 'disconnected',
    configFields: [
      { key: 'webhook', label: 'Webhook URL', type: 'url', placeholder: 'https://hooks.slack.com/…' },
      { key: 'channel', label: 'Channel', type: 'text', placeholder: '#cqr-alerts' },
    ],
  },
  {
    id: 'github-actions',
    name: 'GitHub Actions',
    description: 'Trigger CI/CD pipelines on diff apply.',
    category: 'cicd',
    enabled: false,
    status: 'disconnected',
    configFields: [
      { key: 'token', label: 'GitHub Token', type: 'password', placeholder: 'ghp_…' },
      { key: 'workflow', label: 'Workflow File', type: 'text', placeholder: 'ci.yml' },
    ],
  },
  {
    id: 'anthropic',
    name: 'Anthropic Claude',
    description: 'Use Claude as the primary LLM for agent tasks.',
    category: 'llm',
    enabled: true,
    status: 'connected',
    configFields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-ant-api03-…' },
      { key: 'model', label: 'Model', type: 'text', placeholder: 'claude-opus-4-5' },
    ],
  },
  {
    id: 'openai',
    name: 'OpenAI',
    description: 'Use GPT-4o as an alternative LLM for agent tasks.',
    category: 'llm',
    enabled: false,
    status: 'disconnected',
    configFields: [
      { key: 'api_key', label: 'API Key', type: 'password', placeholder: 'sk-…' },
      { key: 'model', label: 'Model', type: 'text', placeholder: 'gpt-4o' },
    ],
  },
];

const CATEGORY_LABELS: Record<string, string> = {
  vcs: 'Version Control',
  pm: 'Project Management',
  comms: 'Communications',
  cicd: 'CI/CD',
  llm: 'LLM Providers',
};

export const Connectors: React.FC = () => {
  const [connectors, setConnectors] = useState<Connector[]>(DEFAULT_CONNECTORS);
  const [selectedId, setSelectedId] = useState<string | null>('anthropic');
  const [configValues, setConfigValues] = useState<Record<string, Record<string, string>>>({});
  const { toasts, add: addToast, dismiss } = useToast();

  const selected = connectors.find((c) => c.id === selectedId) || null;

  const toggleConnector = (id: string) => {
    setConnectors((prev) =>
      prev.map((c) =>
        c.id === id
          ? { ...c, enabled: !c.enabled, status: !c.enabled ? 'connected' : 'disconnected' }
          : c
      )
    );
    const c = connectors.find((x) => x.id === id);
    if (c) addToast(`${c.name} ${c.enabled ? 'disabled' : 'enabled'}`, 'info');
  };

  const handleSaveConfig = (id: string) => {
    addToast(`Configuration saved for ${connectors.find((c) => c.id === id)?.name}`, 'success');
  };

  const categories = Array.from(new Set(connectors.map((c) => c.category)));

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: connector list */}
      <div style={{
        width: 280, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', display: 'flex', flexDirection: 'column', overflow: 'auto'
      }}>
        <div style={{ padding: 'var(--sp-3) var(--sp-4)', borderBottom: 'var(--border)', flexShrink: 0 }}>
          <span style={{ font: 'var(--type-title)', fontSize: 15 }}>Connectors</span>
        </div>

        {categories.map((cat) => (
          <div key={cat}>
            <div style={{
              padding: 'var(--sp-2) var(--sp-4)',
              fontSize: 10, color: 'var(--text-muted)',
              textTransform: 'uppercase', letterSpacing: '0.06em',
              background: 'var(--bg-base)',
              borderBottom: 'var(--border)',
            }}>
              {CATEGORY_LABELS[cat] || cat}
            </div>
            {connectors.filter((c) => c.category === cat).map((c) => (
              <div
                key={c.id}
                onClick={() => setSelectedId(c.id)}
                style={{
                  padding: 'var(--sp-3) var(--sp-4)',
                  borderBottom: 'var(--border)',
                  cursor: 'pointer',
                  background: c.id === selectedId ? 'var(--raised)' : 'transparent',
                  borderLeft: c.id === selectedId ? '2px solid var(--status-info)' : '2px solid transparent',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                }}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && setSelectedId(c.id)}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{c.name}</div>
                  <StatusChip
                    label={c.status}
                    severity={c.status === 'connected' ? 'success' : c.status === 'error' ? 'danger' : 'neutral'}
                  />
                </div>
                <Toggle
                  checked={c.enabled}
                  onChange={() => toggleConnector(c.id)}
                />
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* Right: config panel */}
      <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-5)' }}>
        {selected ? (
          <div style={{ maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 'var(--sp-5)' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--sp-3)', marginBottom: 'var(--sp-2)' }}>
                <h2 style={{ font: 'var(--type-title)', fontSize: 16 }}>{selected.name}</h2>
                <StatusChip
                  label={selected.status}
                  severity={selected.status === 'connected' ? 'success' : selected.status === 'error' ? 'danger' : 'neutral'}
                />
              </div>
              <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.6 }}>{selected.description}</p>
            </div>

            {selected.configFields && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)' }}>
                <div className="label">Configuration</div>
                {selected.configFields.map((field) => (
                  <div key={field.key}>
                    <label className="label" htmlFor={`${selected.id}-${field.key}`} style={{ display: 'block', marginBottom: 4 }}>
                      {field.label}
                    </label>
                    <input
                      id={`${selected.id}-${field.key}`}
                      type={field.type === 'password' ? 'password' : 'text'}
                      placeholder={field.placeholder}
                      value={configValues[selected.id]?.[field.key] || ''}
                      onChange={(e) =>
                        setConfigValues((prev) => ({
                          ...prev,
                          [selected.id]: { ...prev[selected.id], [field.key]: e.target.value },
                        }))
                      }
                      style={{
                        width: '100%', background: 'var(--raised)', border: 'var(--border)',
                        color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
                        padding: 'var(--sp-2)', outline: 'none'
                      }}
                      className="selectable"
                      autoComplete="off"
                    />
                  </div>
                ))}

                <div style={{ display: 'flex', gap: 'var(--sp-2)', marginTop: 'var(--sp-2)' }}>
                  <Button variant="primary" onClick={() => handleSaveConfig(selected.id)}>
                    Save Configuration
                  </Button>
                  <Toggle
                    checked={selected.enabled}
                    onChange={() => toggleConnector(selected.id)}
                    label={selected.enabled ? 'Enabled' : 'Disabled'}
                  />
                </div>
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
            Select a connector to configure it
          </div>
        )}
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
