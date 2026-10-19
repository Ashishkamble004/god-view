import React from 'react';
import type { BadgeVariant, SeverityLevel } from '../api/types';

const BADGE_STYLES: Record<BadgeVariant, string> = {
  default: 'bg-inverse-surface text-inverse-on-surface border-inverse-surface',
  secondary: 'bg-surface-container text-on-surface border-outline-variant',
  outline: 'bg-surface text-on-surface-variant border-outline-variant',
  muted: 'bg-surface-container-low text-on-surface-variant border-outline-variant',
  success: 'bg-healthy-container text-on-healthy-container border-outline-variant',
  warning: 'bg-warning-container text-on-warning-container border-outline-variant',
  danger: 'bg-incident-container text-on-incident-container border-outline-variant',
  info: 'bg-primary-container text-on-primary-container border-outline-variant',
};

const DOT_STYLES: Record<BadgeVariant, string> = {
  default: 'bg-inverse-on-surface',
  secondary: 'bg-on-surface-variant',
  outline: 'bg-on-surface-variant',
  muted: 'bg-unknown',
  success: 'bg-healthy',
  warning: 'bg-warning',
  danger: 'bg-incident',
  info: 'bg-primary',
};

export interface BadgeProps {
  variant?: BadgeVariant;
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export function Badge({
  variant = 'secondary',
  dot = false,
  children,
  className = '',
}: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 font-label text-xs font-medium leading-5 ${BADGE_STYLES[variant]} ${className}`}
    >
      {dot && <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_STYLES[variant]}`} />}
      {children}
    </span>
  );
}

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md';

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'border-primary bg-primary text-on-primary hover:bg-primary-hover hover:border-primary-hover disabled:border-outline-variant disabled:bg-surface-container disabled:text-on-surface-variant',
  secondary:
    'border-outline-variant bg-surface text-on-surface hover:bg-surface-container-low disabled:border-outline-variant disabled:bg-surface-container disabled:text-on-surface-variant',
  danger:
    'border-incident bg-incident-container text-on-incident-container hover:bg-incident hover:text-on-error disabled:border-outline-variant disabled:bg-surface-container disabled:text-on-surface-variant',
  ghost:
    'border-transparent bg-transparent text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface disabled:border-transparent disabled:bg-transparent disabled:text-on-surface-variant',
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'px-3 py-1 text-xs min-h-[32px]',
  md: 'px-4 py-1.5 text-xs min-h-[36px]',
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', className = '', type = 'button', ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-full border font-label font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-1 disabled:cursor-not-allowed ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export function SeverityBadge({ severity }: { severity: SeverityLevel }) {
  if (severity === 'sev1') {
    return (
      <Badge variant="danger" dot>
        Sev-1
      </Badge>
    );
  }
  if (severity === 'sev2') {
    return (
      <Badge variant="warning" dot>
        Sev-2
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" dot>
      Sev-3
    </Badge>
  );
}

const TRUST_LABELS: Record<number, { label: string; variant: BadgeVariant }> = {
  0: { label: 'L0 Observe', variant: 'muted' },
  1: { label: 'L1 Recommend', variant: 'secondary' },
  2: { label: 'L2 Approval required', variant: 'warning' },
  3: { label: 'L3 Auto-infra', variant: 'info' },
  4: { label: 'L4 Auto-business', variant: 'success' },
};

export function TrustLevelBadge({ level }: { level: number }) {
  const cfg = TRUST_LABELS[level] ?? TRUST_LABELS[0];
  return (
    <Badge variant={cfg.variant} dot>
      {cfg.label}
    </Badge>
  );
}

const ENUM_LABELS: Record<string, string> = {
  pr_opened: 'PR opened',
  pr_merged: 'PR merged',
  deploy: 'Deploy',
  config_flip: 'Config change',
  idle_reasoning_engine: 'Idle reasoning engine',
  oversized: 'Oversized tier',
  unattached: 'Unattached resource',
  log_verboseness: 'Log verbosity',
  consensus_reached: 'Consensus reached',
  clock_expired: 'Clock expired',
  escalated: 'Escalated to Argon',
  sentinel_compiled: 'Sentinel compiled',
  business_line: 'Business line',
  application: 'Application',
  data: 'Data',
  change: 'Change',
  platform: 'Platform',
  cloud_run: 'Cloud Run',
  gke_workload: 'GKE workload',
  compute_vm: 'Compute VM',
  cloud_sql: 'Cloud SQL',
  enforced: 'Enforced',
  shadow: 'Shadow',
  proposed: 'Proposed',
  retired: 'Retired',
  paused: 'Paused',
  pending: 'Pending',
  approved: 'Approved',
  rejected: 'Rejected',
  snoozed: 'Snoozed',
  expired: 'Expired',
  mitigated: 'Mitigated',
  resolved: 'Resolved',
  investigating: 'Investigating',
  escalated_to_human: 'Escalated to human',
  open: 'Open',
  accepted: 'Accepted',
  dismissed: 'Dismissed',
  drifted: 'Drifted',
  restored: 'Restored',
  reconciling: 'Reconciling',
  awake: 'Awake',
  sleeping: 'Sleeping',
  retiring: 'Retiring',
  spawning: 'Spawning',
  healthy: 'Healthy',
  degraded: 'Degraded',
  hibernating: 'Hibernating',
  available: 'Available',
  unavailable: 'Unavailable',
  aligned: 'Aligned',
  skipped: 'Skipped',
  reordered: 'Reordered',
  stalled: 'Stalled',
  passed: 'Passed',
  failed: 'Failed',
  running: 'Running',
};

export function formatEnumLabel(raw: string | null | undefined): string {
  if (!raw) return '—';
  const key = raw.toLowerCase();
  if (ENUM_LABELS[key]) return ENUM_LABELS[key];
  const spaced = raw.replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

export function formatCurrency(amount: number | null | undefined): string {
  const n = Number(amount ?? 0);
  if (Number.isNaN(n)) return '$0';
  if (n > 0 && n < 10) {
    return `$${n.toFixed(2)}`;
  }
  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(Math.round(n));
  return `$${formatted}`;
}

export function formatUsd(amount: number | null | undefined): string {
  const n = Number(amount ?? 0);
  return `$${(Number.isNaN(n) ? 0 : n).toFixed(2)}`;
}

export function formatNumber(val: number | null | undefined): string {
  const n = Number(val ?? 0);
  return new Intl.NumberFormat('en-US').format(Number.isNaN(n) ? 0 : n);
}

export function formatShortTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace('T', ' ').slice(0, 19) + 'Z';
}

export function formatExpiryCountdownIst(
  expiresIso: string | null | undefined,
  referenceIso?: string | null
): string {
  if (!expiresIso) return '—';
  const exp = new Date(expiresIso);
  if (Number.isNaN(exp.getTime())) return expiresIso;

  const ref = referenceIso ? new Date(referenceIso) : new Date('2026-10-16T14:08:40Z');
  const diffMs = Math.max(0, exp.getTime() - ref.getTime());
  const totalMin = Math.round(diffMs / 60000);
  const hours = Math.floor(totalMin / 60);
  const mins = totalMin % 60;

  let istTime = '';
  try {
    istTime = new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(exp);
  } catch {
    istTime = exp.toISOString().slice(11, 16);
  }

  if (totalMin <= 0) {
    return `Expired · ${istTime} IST`;
  }
  return `Expires in ${hours}h ${String(mins).padStart(2, '0')}m · ${istTime} IST`;
}

export function Card({
  children,
  className = '',
  testId,
}: {
  children: React.ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={`rounded-lg border border-outline-variant bg-surface shadow-none ${className}`}
    >
      {children}
    </div>
  );
}

export function SectionHeader({
  title,
  subtitle,
  right,
}: {
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-outline-variant pb-3">
      <div>
        <h2 className="font-title text-base font-medium text-on-surface">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-on-surface-variant">{subtitle}</p>}
      </div>
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  );
}

export function TableSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" aria-label="Loading data">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-9 w-full animate-pulse rounded-sm border border-outline-variant bg-surface-container-low"
        />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-outline-variant bg-surface-container-low p-8 text-center">
      <p className="font-title text-sm font-medium text-on-surface">{title}</p>
      <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
    </div>
  );
}
