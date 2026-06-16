/**
 * CQR Shared Component Library
 * All components follow the PDR §6 specification.
 * Each ships with: default / hover / active / disabled / loading / empty / error states.
 */

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { clsx } from 'clsx';

// ── Button ─────────────────────────────────────────────────────────────────

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost' | 'danger' | 'deploy';
  loading?: boolean;
  children: React.ReactNode;
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  loading = false,
  disabled,
  children,
  className,
  ...props
}) => (
  <button
    className={clsx('cqr-btn', `cqr-btn--${variant}`, { 'cqr-btn--loading': loading }, className)}
    disabled={disabled || loading}
    aria-busy={loading}
    {...props}
  >
    {loading && <Spinner size={12} />}
    {children}
  </button>
);

// ── StatusChip ─────────────────────────────────────────────────────────────

export type Severity = 'danger' | 'warning' | 'success' | 'info' | 'neutral';

interface StatusChipProps {
  label: string;
  severity: Severity;
  className?: string;
}

export const StatusChip: React.FC<StatusChipProps> = ({ label, severity, className }) => (
  <span
    className={clsx('cqr-chip', `cqr-chip--${severity}`, className)}
    role="status"
    aria-label={`${severity}: ${label}`}
  >
    {label}
  </span>
);

// ── Panel ──────────────────────────────────────────────────────────────────

interface PanelProps {
  title?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  variant?: 'surface' | 'raised';
  className?: string;
  style?: React.CSSProperties;
}

export const Panel: React.FC<PanelProps> = ({
  title, actions, children, variant = 'surface', className, style
}) => (
  <div className={clsx('cqr-panel', `cqr-panel--${variant}`, className)} style={style}>
    {(title || actions) && (
      <div className="cqr-panel__header">
        {title && <span className="cqr-panel__title label">{title}</span>}
        {actions && <div className="cqr-panel__actions">{actions}</div>}
      </div>
    )}
    <div className="cqr-panel__body">{children}</div>
  </div>
);

// ── ListRow ────────────────────────────────────────────────────────────────

interface ListRowProps {
  leading?: React.ReactNode;
  trailing?: React.ReactNode;
  status?: Severity;
  selected?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
}

export const ListRow: React.FC<ListRowProps> = ({
  leading, trailing, selected, onClick, children, className
}) => (
  <div
    className={clsx('cqr-listrow', { 'cqr-listrow--selected': selected }, className)}
    onClick={onClick}
    role={onClick ? 'button' : undefined}
    tabIndex={onClick ? 0 : undefined}
    onKeyDown={onClick ? (e) => e.key === 'Enter' && onClick() : undefined}
  >
    {leading && <span className="cqr-listrow__leading">{leading}</span>}
    <span className="cqr-listrow__content">{children}</span>
    {trailing && <span className="cqr-listrow__trailing">{trailing}</span>}
  </div>
);

// ── Card ───────────────────────────────────────────────────────────────────

interface CardProps {
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
  selected?: boolean;
  style?: React.CSSProperties;
}

export const Card: React.FC<CardProps> = ({ onClick, children, className, selected, style }) => (
  <div
    style={style}
    className={clsx('cqr-card', { 'cqr-card--selected': selected }, className)}
    onClick={onClick}
    role={onClick ? 'button' : undefined}
    tabIndex={onClick ? 0 : undefined}
    onKeyDown={onClick ? (e) => e.key === 'Enter' && onClick() : undefined}
  >
    {children}
  </div>
);

// ── DataTable ──────────────────────────────────────────────────────────────

interface Column<T> {
  key: keyof T | string;
  header: string;
  width?: string;
  render?: (row: T) => React.ReactNode;
}

interface DataTableProps<T extends { id: string }> {
  columns: Column<T>[];
  rows: T[];
  onSelect?: (row: T) => void;
  selectedId?: string;
  emptyMessage?: string;
  loading?: boolean;
}

export function DataTable<T extends { id: string }>({
  columns, rows, onSelect, selectedId, emptyMessage = 'No data', loading
}: DataTableProps<T>) {
  if (loading) return <div className="cqr-table__loading"><Spinner /></div>;
  if (rows.length === 0) return (
    <div className="cqr-table__empty mono">{emptyMessage}</div>
  );
  return (
    <div className="cqr-table" role="grid">
      <div className="cqr-table__head" role="row">
        {columns.map((c) => (
          <div key={String(c.key)} className="cqr-table__th label" style={{ width: c.width }} role="columnheader">
            {c.header}
          </div>
        ))}
      </div>
      <div className="cqr-table__body">
        {rows.map((row) => (
          <div
            key={row.id}
            className={clsx('cqr-table__row', { 'cqr-table__row--selected': row.id === selectedId })}
            onClick={() => onSelect?.(row)}
            role="row"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onSelect?.(row)}
          >
            {columns.map((c) => (
              <div key={String(c.key)} className="cqr-table__td" style={{ width: c.width }} role="gridcell">
                {c.render ? c.render(row) : String((row as Record<string, unknown>)[String(c.key)] ?? '')}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Toggle ─────────────────────────────────────────────────────────────────

interface ToggleProps {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  label?: string;
  description?: string;
}

export const Toggle: React.FC<ToggleProps> = ({ checked, onChange, disabled, label, description }) => (
  <label className={clsx('cqr-toggle', { 'cqr-toggle--disabled': disabled })}>
    <span className="cqr-toggle__label">
      {label && <span className="cqr-toggle__name">{label}</span>}
      {description && <span className="cqr-toggle__desc">{description}</span>}
    </span>
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={clsx('cqr-toggle__track', { 'cqr-toggle__track--on': checked })}
      onClick={() => !disabled && onChange(!checked)}
    >
      <span className="cqr-toggle__thumb" />
    </button>
  </label>
);

// ── Slider ─────────────────────────────────────────────────────────────────

interface SliderProps {
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  label?: string;
  disabled?: boolean;
}

export const Slider: React.FC<SliderProps> = ({ min, max, step, value, onChange, label, disabled }) => (
  <div className="cqr-slider">
    {label && <label className="cqr-slider__label label">{label}: <span className="mono">{value.toFixed(2)}</span></label>}
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      className="cqr-slider__input"
    />
  </div>
);

// ── Modal ──────────────────────────────────────────────────────────────────

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  variant?: 'default' | 'destructive';
  children: React.ReactNode;
  actions?: React.ReactNode;
}

export const Modal: React.FC<ModalProps> = ({ open, onClose, title, variant = 'default', children, actions }) => {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="cqr-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="modal-title" onClick={onClose}>
      <div className={clsx('cqr-modal', `cqr-modal--${variant}`)} onClick={(e) => e.stopPropagation()}>
        <div className="cqr-modal__header">
          <span id="modal-title" className="cqr-modal__title">{title}</span>
          <button className="cqr-modal__close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="cqr-modal__body">{children}</div>
        {actions && <div className="cqr-modal__footer">{actions}</div>}
      </div>
    </div>
  );
};

// ── Toast ──────────────────────────────────────────────────────────────────

interface ToastItem {
  id: string;
  type: 'info' | 'success' | 'error';
  message: string;
}

interface ToastContainerProps {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

export const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, onDismiss }) => (
  <div className="cqr-toast-container" role="region" aria-live="polite" aria-label="Notifications">
    {toasts.map((t) => (
      <div key={t.id} className={clsx('cqr-toast', `cqr-toast--${t.type}`)}>
        <span>{t.message}</span>
        <button onClick={() => onDismiss(t.id)} aria-label="Dismiss">✕</button>
      </div>
    ))}
  </div>
);

// Toast hook
export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const add = useCallback((message: string, type: ToastItem['type'] = 'info') => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }, []);
  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);
  return { toasts, add, dismiss };
}

// ── EmptyState ─────────────────────────────────────────────────────────────

interface EmptyStateProps {
  title: string;
  description?: string;
  cta?: { label: string; onClick: () => void };
  icon?: React.ReactNode;
}

export const EmptyState: React.FC<EmptyStateProps> = ({ title, description, cta, icon }) => (
  <div className="cqr-empty">
    {icon && <div className="cqr-empty__icon">{icon}</div>}
    <div className="cqr-empty__title">{title}</div>
    {description && <div className="cqr-empty__desc">{description}</div>}
    {cta && <Button variant="ghost" onClick={cta.onClick} className="cqr-empty__cta">{cta.label}</Button>}
  </div>
);

// ── Spinner ────────────────────────────────────────────────────────────────

interface SpinnerProps { size?: number; }

export const Spinner: React.FC<SpinnerProps> = ({ size = 16 }) => (
  <svg
    className="cqr-spinner"
    width={size}
    height={size}
    viewBox="0 0 16 16"
    aria-hidden="true"
  >
    <circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="28" strokeDashoffset="10" />
  </svg>
);

// ── ProgressBar ────────────────────────────────────────────────────────────

interface ProgressBarProps {
  value?: number; // 0-100; undefined = indeterminate
  label?: string;
}

export const ProgressBar: React.FC<ProgressBarProps> = ({ value, label }) => (
  <div className="cqr-progress" role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
    <div
      className={clsx('cqr-progress__bar', { 'cqr-progress__bar--indeterminate': value === undefined })}
      style={value !== undefined ? { width: `${value}%` } : undefined}
    />
    {label && <span className="cqr-progress__label mono">{label}{value !== undefined ? ` ${value}%` : ''}</span>}
  </div>
);

// ── MetricReadout ──────────────────────────────────────────────────────────

interface MetricReadoutProps {
  value: string | number;
  unit?: string;
  delta?: string;
  label?: string;
}

export const MetricReadout: React.FC<MetricReadoutProps> = ({ value, unit, delta, label }) => (
  <div className="cqr-metric">
    {label && <div className="cqr-metric__label label">{label}</div>}
    <div className="cqr-metric__value mono">
      {value}
      {unit && <span className="cqr-metric__unit">{unit}</span>}
      {delta && <span className={clsx('cqr-metric__delta', delta.startsWith('-') ? 'cqr-metric__delta--neg' : 'cqr-metric__delta--pos')}>{delta}</span>}
    </div>
  </div>
);

// ── SkeletonCard ───────────────────────────────────────────────────────────

export const SkeletonCard: React.FC = () => (
  <div className="cqr-card skeleton" style={{ height: 120 }} aria-hidden="true" />
);

// ── Tooltip ────────────────────────────────────────────────────────────────

interface TooltipProps {
  content: string;
  children: React.ReactElement;
}

export const Tooltip: React.FC<TooltipProps> = ({ content, children }) => {
  const [show, setShow] = useState(false);
  return (
    <span
      className="cqr-tooltip-wrap"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
      style={{ position: 'relative', display: 'inline-flex' }}
    >
      {children}
      {show && (
        <span className="cqr-tooltip" role="tooltip">{content}</span>
      )}
    </span>
  );
};
