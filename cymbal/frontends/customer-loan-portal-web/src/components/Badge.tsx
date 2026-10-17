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

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  default:
    'bg-gblue-tint text-gblue border-gblue-border dark:bg-slate-800 dark:text-blue-200',
  secondary:
    'bg-slate-100 text-slate-800 border-slate-300 dark:bg-slate-800 dark:text-slate-200',
  outline:
    'bg-white text-slate-700 border-slate-300 dark:bg-slate-900 dark:text-slate-200',
  muted:
    'bg-slate-50 text-slate-600 border-slate-200 dark:bg-slate-800/70 dark:text-slate-300',
  success:
    'bg-ggreen-tint text-ggreen border-ggreen-border dark:bg-emerald-950 dark:text-emerald-200',
  warning:
    'bg-gamber-tint text-gamber border-gamber-border dark:bg-amber-950 dark:text-amber-200',
  danger:
    'bg-gred-tint text-gred border-gred-border dark:bg-red-950 dark:text-red-200',
  info:
    'bg-gblue-tint text-gblue border-gblue-border dark:bg-blue-950 dark:text-blue-200',
};

const DOT_CLASSES: Record<BadgeVariant, string> = {
  default: 'bg-gblue',
  secondary: 'bg-slate-600',
  outline: 'bg-slate-500',
  muted: 'bg-slate-500',
  success: 'bg-ggreen',
  warning: 'bg-gamber-raw',
  danger: 'bg-gred',
  info: 'bg-gblue-bright',
};

export function mapStatusToBadgeVariant(status: string): BadgeVariant {
  switch (status) {
    case 'DISBURSED':
    case 'APPROVED':
    case 'INSURED':
    case 'KYC_VERIFIED':
    case 'COMPLETED':
    case 'CLEAN':
      return 'success';
    case 'KYC_PENDING':
    case 'DISBURSEMENT_PENDING':
    case 'IN_PROGRESS':
    case 'PENDING':
      return 'warning';
    case 'MODEL_ARMOR_BLOCKED':
    case 'REJECTED':
    case 'FAILED':
    case 'BLOCKED':
      return 'danger';
    case 'SKIPPED':
      return 'muted';
    case 'REPLAYED':
      return 'info';
    default:
      return 'secondary';
  }
}

export interface BadgeProps {
  variant?: BadgeVariant;
  dot?: boolean;
  children: React.ReactNode;
  className?: string;
}

export const Badge: React.FC<BadgeProps> = ({
  variant = 'default',
  dot = true,
  children,
  className = '',
}) => {
  const safeVariant: BadgeVariant = ALLOWED_BADGE_VARIANTS.includes(variant)
    ? variant
    : 'secondary';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-semibold border whitespace-nowrap ${VARIANT_CLASSES[safeVariant]} ${className}`}
    >
      {dot && (
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${DOT_CLASSES[safeVariant]}`}
        />
      )}
      {children}
    </span>
  );
};
