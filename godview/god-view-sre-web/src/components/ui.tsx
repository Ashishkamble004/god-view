import React from 'react';
import type { BadgeVariant, SeverityLevel } from '../api/types';

const BADGE_STYLES: Record<BadgeVariant, string> = {
  default: 'bg-slate-900 text-white border-slate-900',
  secondary: 'bg-slate-100 text-slate-800 border-slate-200',
  outline: 'bg-white text-slate-700 border-slate-300',
  muted: 'bg-slate-50 text-slate-600 border-slate-200',
  success: 'bg-green-50 text-green-800 border-green-200',
  warning: 'bg-amber-50 text-amber-900 border-amber-200',
  danger: 'bg-red-50 text-red-800 border-red-200',
  info: 'bg-blue-50 text-blue-800 border-blue-200',
};

const DOT_STYLES: Record<BadgeVariant, string> = {
  default: 'bg-white',
  secondary: 'bg-slate-500',
  outline: 'bg-slate-500',
  muted: 'bg-slate-500',
  success: 'bg-green-600',
  warning: 'bg-amber-600',
  danger: 'bg-red-600',
  info: 'bg-blue-600',
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
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded border px-2 py-0.5 text-xs font-semibold leading-5 ${BADGE_STYLES[variant]} ${className}`}
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
    'border-blue-600 bg-blue-600 text-white hover:bg-blue-700 hover:border-blue-700 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-500',
  secondary:
    'border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-500',
  danger:
    'border-red-600 bg-red-600 text-white hover:bg-red-700 hover:border-red-700 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-500',
  ghost:
    'border-transparent bg-transparent text-slate-700 hover:bg-slate-100 disabled:border-transparent disabled:bg-transparent disabled:text-slate-500',
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1 text-xs',
  md: 'px-3 py-1.5 text-xs',
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
        className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded border font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-blue-600 focus:ring-offset-1 disabled:cursor-not-allowed ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
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

export function formatInr(amount: number): string {
  const formatted = new Intl.NumberFormat('en-IN', {
    maximumFractionDigits: 0,
  }).format(Math.round(amount));
  return `₹${formatted}`;
}

export function formatUsd(amount: number): string {
  return `$${amount.toFixed(2)}`;
}

export function formatNumber(val: number): string {
  return new Intl.NumberFormat('en-US').format(val);
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
      className={`rounded-md border border-slate-200 bg-white shadow-sm ${className}`}
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
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 pb-3">
      <div>
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
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
          className="h-9 w-full animate-pulse rounded border border-slate-100 bg-slate-50"
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
    <div className="rounded-md border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      <p className="mt-1 text-xs text-slate-500">{description}</p>
    </div>
  );
}
