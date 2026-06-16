/**
 * CQR Application Shell — §5
 * Persistent shell wrapping every screen.
 * - Custom frameless window chrome
 * - Left rail primary nav (context-aware)
 * - Top bar (project + session ID + agent state)
 * - Status bar (bottom)
 * - Command palette (Cmd/Ctrl-K)
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { clsx } from 'clsx';
import { useSessionStore, useAgentStore, useScanStore } from '../../stores';
import { StatusChip, Tooltip } from '../index';

// ── Saturn Logo Mark (SVG inline) ──────────────────────────────────────────

const SaturnMark: React.FC<{ size?: number }> = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="CQR Saturn mark">
    <circle cx="12" cy="12" r="4" fill="var(--text)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="1" fill="none" transform="rotate(-20 12 12)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="0.7" fill="none" opacity="0.5" transform="rotate(-30 12 12)" />
    <ellipse cx="12" cy="12" rx="10" ry="3.5" stroke="var(--text)" strokeWidth="0.5" fill="none" opacity="0.3" transform="rotate(-10 12 12)" />
  </svg>
);

// ── Nav items ──────────────────────────────────────────────────────────────

interface NavItem {
  id: string;
  label: string;
  shortcut: string;
  path: string;
  icon: React.ReactNode;
  requiresProject?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: 'hub',
    label: 'Project Hub',
    shortcut: '⌘1',
    path: '/',
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <rect x="1" y="1" width="6" height="6" stroke="currentColor" strokeWidth="1.2" />
        <rect x="9" y="1" width="6" height="6" stroke="currentColor" strokeWidth="1.2" />
        <rect x="1" y="9" width="6" height="6" stroke="currentColor" strokeWidth="1.2" />
        <rect x="9" y="9" width="6" height="6" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
  {
    id: 'ide',
    label: 'IDE',
    shortcut: '⌘2',
    path: '/ide',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <polyline points="4,5 1,8 4,11" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <polyline points="12,5 15,8 12,11" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <line x1="9" y1="3" x2="7" y2="13" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
  {
    id: 'kg',
    label: 'KG Explorer',
    shortcut: '⌘3',
    path: '/kg',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.2" />
        <circle cx="3" cy="4" r="1.5" stroke="currentColor" strokeWidth="1" />
        <circle cx="13" cy="4" r="1.5" stroke="currentColor" strokeWidth="1" />
        <circle cx="3" cy="12" r="1.5" stroke="currentColor" strokeWidth="1" />
        <circle cx="13" cy="12" r="1.5" stroke="currentColor" strokeWidth="1" />
        <line x1="6" y1="7" x2="4.2" y2="5" stroke="currentColor" strokeWidth="0.8" />
        <line x1="10" y1="7" x2="11.8" y2="5" stroke="currentColor" strokeWidth="0.8" />
        <line x1="6" y1="9" x2="4.2" y2="11" stroke="currentColor" strokeWidth="0.8" />
        <line x1="10" y1="9" x2="11.8" y2="11" stroke="currentColor" strokeWidth="0.8" />
      </svg>
    ),
  },
  {
    id: 'lsm',
    label: 'LSM View',
    shortcut: '⌘4',
    path: '/lsm',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="2" fill="currentColor" />
        <circle cx="8" cy="8" r="5" stroke="currentColor" strokeWidth="0.8" strokeDasharray="2 1" />
        <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="0.6" strokeDasharray="1.5 1.5" opacity="0.5" />
      </svg>
    ),
  },
  {
    id: 'security',
    label: 'Security',
    shortcut: '⌘5',
    path: '/security',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <path d="M8 1L14 4V9C14 12.3 11.3 15 8 15C4.7 15 2 12.3 2 9V4L8 1Z" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <line x1="8" y1="6" x2="8" y2="9" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="8" cy="11" r="0.8" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: 'vault',
    label: 'Vault',
    shortcut: '⌘6',
    path: '/vault',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <rect x="2" y="5" width="12" height="9" rx="0" stroke="currentColor" strokeWidth="1.2" />
        <path d="M5 5V4C5 2.3 11 2.3 11 4V5" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <circle cx="8" cy="9.5" r="1.5" stroke="currentColor" strokeWidth="1" />
      </svg>
    ),
  },
  {
    id: 'sandbox',
    label: 'Sandbox',
    shortcut: '⌘7',
    path: '/sandbox',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <rect x="1" y="3" width="14" height="10" stroke="currentColor" strokeWidth="1.2" />
        <line x1="1" y1="6" x2="15" y2="6" stroke="currentColor" strokeWidth="0.8" />
        <line x1="4" y1="9" x2="7" y2="9" stroke="currentColor" strokeWidth="1" />
        <line x1="4" y1="11" x2="9" y2="11" stroke="currentColor" strokeWidth="1" />
      </svg>
    ),
  },
  {
    id: 'deploy',
    label: 'Deploy Gate',
    shortcut: '⌘8',
    path: '/deploy',
    requiresProject: true,
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <polygon points="8,2 14,14 2,14" stroke="currentColor" strokeWidth="1.2" fill="none" />
        <line x1="8" y1="7" x2="8" y2="10" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="8" cy="12" r="0.8" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: 'connectors',
    label: 'Connectors',
    shortcut: '⌘9',
    path: '/connectors',
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="4" cy="8" r="2" stroke="currentColor" strokeWidth="1.2" />
        <circle cx="12" cy="8" r="2" stroke="currentColor" strokeWidth="1.2" />
        <line x1="6" y1="8" x2="10" y2="8" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
  {
    id: 'settings',
    label: 'Settings',
    shortcut: '⌘,',
    path: '/settings',
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="2.5" stroke="currentColor" strokeWidth="1.2" />
        <path d="M8 1V3M8 13V15M1 8H3M13 8H15M3.2 3.2L4.6 4.6M11.4 11.4L12.8 12.8M12.8 3.2L11.4 4.6M4.6 11.4L3.2 12.8" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    ),
  },
];

// ── Command Palette ────────────────────────────────────────────────────────

interface PaletteItem {
  id: string;
  label: string;
  shortcut?: string;
  action: () => void;
}

const CommandPalette: React.FC<{
  open: boolean;
  onClose: () => void;
  items: PaletteItem[];
}> = ({ open, onClose, items }) => {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);

  const filtered = items.filter((i) =>
    i.label.toLowerCase().includes(query.toLowerCase())
  );

  useEffect(() => {
    if (!open) { setQuery(''); setSelected(0); }
  }, [open]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!open) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); setSelected((s) => Math.min(s + 1, filtered.length - 1)); }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setSelected((s) => Math.max(s - 1, 0)); }
      if (e.key === 'Enter') {
        e.preventDefault();
        filtered[selected]?.action();
        onClose();
      }
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, filtered, selected, onClose]);

  if (!open) return null;
  return (
    <div className="palette-overlay" onClick={onClose} role="dialog" aria-label="Command palette">
      <div className="palette-box" onClick={(e) => e.stopPropagation()}>
        <input
          className="palette-input"
          placeholder="Search commands…"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setSelected(0); }}
          autoFocus
          aria-label="Command search"
        />
        <div className="palette-results" role="listbox">
          {filtered.map((item, i) => (
            <div
              key={item.id}
              className={clsx('palette-item', { selected: i === selected })}
              onClick={() => { item.action(); onClose(); }}
              role="option"
              aria-selected={i === selected}
            >
              <span>{item.label}</span>
              {item.shortcut && <span className="palette-item__shortcut">{item.shortcut}</span>}
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="palette-item" style={{ color: 'var(--text-muted)' }}>No results</div>
          )}
        </div>
      </div>
    </div>
  );
};

// ── App Shell ──────────────────────────────────────────────────────────────

interface AppShellProps {
  children: React.ReactNode;
}

export const AppShell: React.FC<AppShellProps> = ({ children }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { activeProject, isBackendOnline, agentState } = useSessionStore();
  const { activityLog } = useAgentStore();
  const { findings } = useScanStore();
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Cmd/Ctrl-K to open palette
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const paletteItems: PaletteItem[] = [
    ...NAV_ITEMS.map((n) => ({
      id: `nav-${n.id}`,
      label: `Go to ${n.label}`,
      shortcut: n.shortcut,
      action: () => navigate(n.path),
    })),
    { id: 'run-scan', label: 'Run Security Scan', action: () => navigate('/security') },
    { id: 'open-kg', label: 'Open KG Explorer', action: () => navigate('/kg') },
    { id: 'vault-inject', label: 'Inject Secret', action: () => navigate('/vault') },
    { id: 'deploy', label: 'Open Deploy Gate', action: () => navigate('/deploy') },
  ];

  const criticalCount = findings.filter((f) => f.severity === 'critical').length;
  const latestActivity = activityLog[activityLog.length - 1];

  return (
    <div className="app-shell">
      {/* Offline banner */}
      {!isBackendOnline && (
        <div className="offline-banner" role="alert">
          Runtime not reachable — retrying…
        </div>
      )}

      {/* Top bar */}
      <header className="topbar" role="banner">
        <SaturnMark size={20} />
        <span className="topbar__title">CQR</span>
        {activeProject && (
          <>
            <span className="topbar__session" aria-label={`Project: ${activeProject.name}`}>
              {activeProject.name}
            </span>
            <span className="topbar__session" style={{ opacity: 0.5 }}>
              {activeProject.id.slice(0, 8)}
            </span>
          </>
        )}
        <div className="topbar__spacer" />
        <div className="topbar__controls">
          <StatusChip
            label={agentState}
            severity={agentState === 'working' ? 'info' : agentState === 'blocked' ? 'warning' : 'neutral'}
          />
          {criticalCount > 0 && (
            <StatusChip label={`${criticalCount} critical`} severity="danger" />
          )}
        </div>
      </header>

      {/* Left rail */}
      <nav className="rail" role="navigation" aria-label="Primary navigation">
        <div className="rail__logo" aria-hidden="true">
          <SaturnMark size={28} />
        </div>
        {NAV_ITEMS.map((item) => {
          const disabled = item.requiresProject && !activeProject;
          const active = location.pathname === item.path ||
            (item.path !== '/' && location.pathname.startsWith(item.path));
          return (
            <Tooltip key={item.id} content={`${item.label} ${item.shortcut}`}>
              <button
                className={clsx('rail__nav-item', { active, disabled })}
                onClick={() => !disabled && navigate(item.path)}
                aria-label={item.label}
                aria-current={active ? 'page' : undefined}
                aria-disabled={disabled}
                tabIndex={disabled ? -1 : 0}
              >
                {item.icon}
              </button>
            </Tooltip>
          );
        })}
        <div className="rail__spacer" />
      </nav>

      {/* Main content */}
      <main className="content" role="main">
        {children}
      </main>

      {/* Status bar */}
      <footer className="statusbar" role="contentinfo">
        <span className="statusbar__left truncate">
          {latestActivity ? latestActivity.message : (activeProject ? activeProject.repo_path : '—')}
        </span>
        <span className="statusbar__center">
          {agentState === 'working' ? '● working' : agentState === 'blocked' ? '⚠ blocked' : '○ idle'}
        </span>
        <div className="statusbar__right">
          {criticalCount > 0 && <span style={{ color: 'var(--status-danger)' }}>{criticalCount} critical</span>}
          {activeProject && <span style={{ color: 'var(--status-success)' }}>● KG synced</span>}
        </div>
      </footer>

      {/* Command palette */}
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        items={paletteItems}
      />
    </div>
  );
};
