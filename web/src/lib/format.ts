import type {
  CompensationOut,
  Eligibility,
  JobStatus,
  OrderBy,
  VerificationTier,
} from '../types';

/** Human labels. Kept out of components so the wording is changed in one place. */

export const ELIGIBILITY_LABELS: Record<Eligibility, string> = {
  eligible: 'Eligible',
  unknown: 'Unknown',
  ineligible: 'Ineligible',
};

export const STATUS_LABELS: Record<JobStatus, string> = {
  new: 'New',
  shortlisted: 'Shortlisted',
  applied: 'Applied',
  rejected: 'Rejected',
  dismissed: 'Dismissed',
};

export const VERIFICATION_LABELS: Record<VerificationTier, string> = {
  ok: 'Live',
  suspicious: 'Suspicious',
  scam: 'Flagged',
  expired: 'Expired',
  unknown: 'Unchecked',
};

export const ORDER_BY_LABELS: Record<OrderBy, string> = {
  match_score: 'Match score',
  prescore: 'Pre-score',
  posted_at: 'Date posted',
  last_seen: 'Last seen',
};

/**
 * Whether scoring is effectively switched off.
 *
 * Two spellings reach the wire: `MetaOut` falls back to `"none"` when there is
 * no provider at all, but the composition root always builds *something*, and
 * an unconfigured install gets `NullProvider`, whose `name` is `"null"`. Both
 * mean the same thing to the user.
 */
export function hasNoScoringProvider(provider: string | undefined): boolean {
  return provider === 'none' || provider === 'null';
}

export function titleCase(value: string): string {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** `null` for a date the pipeline never resolved — rendered as an em dash. */
export function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString();
}

/** Rough relative age, for the list where precision is not the point. */
export function relativeDays(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/**
 * Compensation is never inferred on the Python side, so a missing number here
 * genuinely means the listing did not say — show nothing rather than a zero.
 */
export function formatCompensation(comp: CompensationOut): string | null {
  const { minimum, maximum, currency, period } = comp;
  if (minimum === null && maximum === null) return null;

  const number = (value: number): string =>
    value >= 1000
      ? `${Math.round(value / 1000)}k`
      : String(Math.round(value * 100) / 100);

  const range =
    minimum !== null && maximum !== null && minimum !== maximum
      ? `${number(minimum)}–${number(maximum)}`
      : number((minimum ?? maximum) as number);

  const suffix = period ? `/${period.replace(/ly$/, '')}` : '';
  return `${currency ? `${currency} ` : ''}${range}${suffix}`;
}
