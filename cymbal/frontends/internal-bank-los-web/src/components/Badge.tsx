import React from 'react';

export const ALLOWED_BADGE_VARIANTS = [
  'default',
  'secondary',
  'outline',
  'muted',
  'success',
  'warning',
  'danger',
  'info',
] as const;

export type BadgeVariant = (typeof ALLOWED_BADGE_VARIANTS)[number];

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  default:
    'bg-ink-heading text-white border-ink-heading dark:bg-slate-100 dark:text-slate-900 dark:border-slate-100',
  secondary:
    'bg-slate-100 text-ink-body border-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:border-slate-700',
  outline:
    'bg-white text-ink-body border-slate-300 dark:bg-slate-900 dark:text-slate-200 dark:border-slate-700',
  muted:
    'bg-surface text-ink-muted border-slate-200 dark:bg-slate-800/70 dark:text-slate-300 dark:border-slate-700',
  success:
    'bg-ggreen-tint text-ggreen border-ggreen-border dark:bg-emerald-950/60 dark:text-emerald-200 dark:border-emerald-800',
  warning:
    'bg-gamber-tint text-gamber border-gamber-border dark:bg-amber-950/60 dark:text-amber-200 dark:border-amber-800',
  danger:
    'bg-gred-tint text-gred border-gred-border dark:bg-red-950/60 dark:text-red-200 dark:border-red-800',
  info:
    'bg-gblue-tint text-gblue border-gblue-border dark:bg-blue-950/60 dark:text-blue-200 dark:border-blue-800',
};

export function mapStatusToBadgeVariant(status: string): BadgeVariant {
  const normalized = status.toUpperCase().trim();
  switch (normalized) {
    case 'APPROVED':
    case 'INSURED':
    case 'DISBURSED':
    case 'KYC_VERIFIED':
    case 'CLEAN':
    case 'HEALTHY':
    case 'REPLAYED':
    case 'ISSUED':
    case 'ACTIVE':
      return 'success';
    case 'KYC_PENDING':
    case 'DISBURSEMENT_PENDING':
    case 'PENDING':
    case 'DEGRADED':
      return 'warning';
    case 'REJECTED':
    case 'MODEL_ARMOR_BLOCKED':
    case 'BLOCKED':
    case 'FAILED':
    case 'DOWN':
      return 'danger';
    case 'SYNTHETIC':
    case 'EV_SUV':
    case 'COMMERCIAL_FLEET_TOPUP':
    case 'IMAGE':
      return 'info';
    case 'RETAIL_AUTO_LOAN':
    case 'PASSENGER_SUV':
    case 'HUMAN':
      return 'secondary';
    case 'TWO_WHEELER':
    case 'COMMERCIAL_FLEET':
    case 'TEXT_ONLY':
      return 'outline';
    default:
      return 'muted';
  }
}

export interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
  title?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = 'default',
  children,
  className = '',
  title,
}) => {
  const safeVariant: BadgeVariant = ALLOWED_BADGE_VARIANTS.includes(variant)
    ? variant
    : 'muted';

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium border tabular-nums tracking-tight whitespace-nowrap ${VARIANT_STYLES[safeVariant]} ${className}`}
    >
      {children}
    </span>
  );
};
