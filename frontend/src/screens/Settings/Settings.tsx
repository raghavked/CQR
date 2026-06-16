/**
 * Settings — §7.11
 * Global preferences: theme, font size, LLM defaults, security policy.
 */

import React, { useState } from 'react';
import { Toggle, Slider, Button, ToastContainer, useToast } from '../../components';

interface SettingsState {
  theme: 'dark' | 'system';
  fontSize: number;
  monoFont: string;
  defaultModel: string;
  autoScan: boolean;
  blockOnCritical: boolean;
  requireConfirmApply: boolean;
  lsmThreshold: number;
  maxTokenBudget: number;
  telemetry: boolean;
}

const DEFAULTS: SettingsState = {
  theme: 'dark',
  fontSize: 13,
  monoFont: 'JetBrains Mono',
  defaultModel: 'claude-opus-4-5',
  autoScan: true,
  blockOnCritical: true,
  requireConfirmApply: true,
  lsmThreshold: 0.7,
  maxTokenBudget: 100000,
  telemetry: false,
};

const SECTIONS = ['Appearance', 'Editor', 'Agent', 'Security', 'Privacy'];

export const Settings: React.FC = () => {
  const [settings, setSettings] = useState<SettingsState>(DEFAULTS);
  const [activeSection, setActiveSection] = useState('Appearance');
  const { toasts, add: addToast, dismiss } = useToast();

  const set = <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    // Persist to localStorage (Electron will also sync via IPC in production)
    localStorage.setItem('cqr-settings', JSON.stringify(settings));
    addToast('Settings saved', 'success');
  };

  const handleReset = () => {
    setSettings(DEFAULTS);
    localStorage.removeItem('cqr-settings');
    addToast('Settings reset to defaults', 'info');
  };

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden' }}>
      {/* Left: section nav */}
      <div style={{
        width: 180, flexShrink: 0, borderRight: 'var(--border)',
        background: 'var(--surface)', padding: 'var(--sp-4) 0', overflow: 'auto'
      }}>
        <div style={{ padding: '0 var(--sp-4)', marginBottom: 'var(--sp-3)' }}>
          <span style={{ font: 'var(--type-title)', fontSize: 15 }}>Settings</span>
        </div>
        {SECTIONS.map((s) => (
          <div
            key={s}
            onClick={() => setActiveSection(s)}
            style={{
              padding: 'var(--sp-2) var(--sp-4)',
              cursor: 'pointer',
              fontSize: 13,
              color: activeSection === s ? 'var(--text)' : 'var(--text-muted)',
              background: activeSection === s ? 'var(--raised)' : 'transparent',
              borderLeft: activeSection === s ? '2px solid var(--status-info)' : '2px solid transparent',
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && setActiveSection(s)}
          >
            {s}
          </div>
        ))}
      </div>

      {/* Right: section content */}
      <div style={{ flex: 1, overflow: 'auto', padding: 'var(--sp-6)' }}>
        <div style={{ maxWidth: 480, display: 'flex', flexDirection: 'column', gap: 'var(--sp-5)' }}>

          {activeSection === 'Appearance' && (
            <>
              <h2 style={{ font: 'var(--type-title)' }}>Appearance</h2>
              <div>
                <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Theme</div>
                <div style={{ display: 'flex', gap: 'var(--sp-2)' }}>
                  {(['dark', 'system'] as const).map((t) => (
                    <Button
                      key={t}
                      variant={settings.theme === t ? 'primary' : 'ghost'}
                      onClick={() => set('theme', t)}
                      style={{ fontSize: 12 }}
                    >
                      {t}
                    </Button>
                  ))}
                </div>
              </div>
              <Slider
                min={11}
                max={18}
                step={1}
                value={settings.fontSize}
                onChange={(v) => set('fontSize', v)}
                label="UI Font Size"
              />
              <div>
                <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Monospace Font</div>
                <input
                  type="text"
                  value={settings.monoFont}
                  onChange={(e) => set('monoFont', e.target.value)}
                  style={{
                    width: '100%', background: 'var(--raised)', border: 'var(--border)',
                    color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
                    padding: 'var(--sp-2)', outline: 'none'
                  }}
                  className="selectable"
                />
              </div>
            </>
          )}

          {activeSection === 'Editor' && (
            <>
              <h2 style={{ font: 'var(--type-title)' }}>Editor</h2>
              <Slider
                min={11}
                max={20}
                step={1}
                value={settings.fontSize}
                onChange={(v) => set('fontSize', v)}
                label="Editor Font Size"
              />
            </>
          )}

          {activeSection === 'Agent' && (
            <>
              <h2 style={{ font: 'var(--type-title)' }}>Agent</h2>
              <div>
                <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Default Model</div>
                <input
                  type="text"
                  value={settings.defaultModel}
                  onChange={(e) => set('defaultModel', e.target.value)}
                  style={{
                    width: '100%', background: 'var(--raised)', border: 'var(--border)',
                    color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
                    padding: 'var(--sp-2)', outline: 'none'
                  }}
                  className="selectable"
                />
              </div>
              <Slider
                min={0}
                max={1}
                step={0.01}
                value={settings.lsmThreshold}
                onChange={(v) => set('lsmThreshold', v)}
                label="Default LSM Threshold"
              />
              <div>
                <div className="label" style={{ marginBottom: 'var(--sp-2)' }}>Max Token Budget</div>
                <input
                  type="number"
                  value={settings.maxTokenBudget}
                  onChange={(e) => set('maxTokenBudget', Number(e.target.value))}
                  style={{
                    width: '100%', background: 'var(--raised)', border: 'var(--border)',
                    color: 'var(--text)', fontFamily: 'var(--font-mono)', fontSize: 12,
                    padding: 'var(--sp-2)', outline: 'none'
                  }}
                  className="selectable"
                />
              </div>
              <Toggle
                checked={settings.requireConfirmApply}
                onChange={(v) => set('requireConfirmApply', v)}
                label="Require confirmation before applying diffs"
                description="Show a confirmation dialog before applying agent changes"
              />
            </>
          )}

          {activeSection === 'Security' && (
            <>
              <h2 style={{ font: 'var(--type-title)' }}>Security</h2>
              <Toggle
                checked={settings.autoScan}
                onChange={(v) => set('autoScan', v)}
                label="Auto-scan on task completion"
                description="Run a security scan automatically after each agent task"
              />
              <Toggle
                checked={settings.blockOnCritical}
                onChange={(v) => set('blockOnCritical', v)}
                label="Block deploy on critical findings"
                description="Prevent applying diffs when critical vulnerabilities are detected"
              />
            </>
          )}

          {activeSection === 'Privacy' && (
            <>
              <h2 style={{ font: 'var(--type-title)' }}>Privacy</h2>
              <Toggle
                checked={settings.telemetry}
                onChange={(v) => set('telemetry', v)}
                label="Usage telemetry"
                description="Send anonymous usage data to improve CQR (no code or secrets)"
              />
              <div style={{ padding: 'var(--sp-3)', background: 'var(--raised)', border: 'var(--border)', fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                CQR processes all code locally. No source code, secrets, or diffs are ever sent to external servers except the LLM API you configure. The Vault encrypts secrets at rest and never logs values.
              </div>
            </>
          )}

          {/* Save / Reset */}
          <div style={{ display: 'flex', gap: 'var(--sp-2)', paddingTop: 'var(--sp-3)', borderTop: 'var(--border)' }}>
            <Button variant="ghost" onClick={handleReset}>Reset to Defaults</Button>
            <Button variant="primary" onClick={handleSave}>Save Settings</Button>
          </div>
        </div>
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismiss} />
    </div>
  );
};
