import type { Eligibility, VerificationTier } from '../types';

/**
 * The visual language for the tri-state verdict (ADR-0002).
 *
 * Centralised because the rule that matters is a *negative* one and it is easy
 * to break by accident in a single component: `unknown` must not read as an
 * error or a warning to be cleared. It is the expected outcome for any listing
 * whose prose our rules could not resolve, it is shown by default, and the UI
 * must never nudge the user into hiding it — that would defeat the guarantee
 * the whole product rests on.
 *
 * So `unknown` gets a soft, low-saturation amber with an *informational* glyph
 * rather than an alert triangle, and it sits at full text contrast. It is
 * distinct from `eligible`, not subordinate to it.
 *
 * Colour is never the only carrier: every badge pairs its palette with a text
 * label and a shape-distinct icon.
 */
export const ELIGIBILITY_STYLES: Record<
  Eligibility,
  { badge: string; dot: string; accent: string }
> = {
  eligible: {
    badge:
      'bg-emerald-50 text-emerald-800 ring-emerald-600/25 ' +
      'dark:bg-emerald-400/10 dark:text-emerald-200 dark:ring-emerald-400/30',
    dot: 'bg-emerald-600 dark:bg-emerald-400',
    accent: 'border-l-emerald-500',
  },
  unknown: {
    badge:
      'bg-amber-50 text-amber-800 ring-amber-600/25 ' +
      'dark:bg-amber-400/10 dark:text-amber-200 dark:ring-amber-400/30',
    dot: 'bg-amber-500 dark:bg-amber-400',
    accent: 'border-l-amber-400',
  },
  ineligible: {
    badge:
      'bg-rose-50 text-rose-800 ring-rose-600/20 ' +
      'dark:bg-rose-400/10 dark:text-rose-200 dark:ring-rose-400/25',
    dot: 'bg-rose-500 dark:bg-rose-400',
    accent: 'border-l-rose-400',
  },
};

/** Shown on hover/focus of a badge. The `unknown` copy does the real work. */
export const ELIGIBILITY_HELP: Record<Eligibility, string> = {
  eligible: 'The rules found positive evidence that you qualify.',
  unknown:
    'The listing did not say enough either way. This is a normal outcome, not ' +
    'a problem — Outpost shows these rather than guessing, because a job ' +
    'hidden by a rule that missed is one you would never find out about.',
  ineligible: 'The rules found positive evidence of a blocker.',
};

export const VERIFICATION_STYLES: Record<VerificationTier, string> = {
  ok: 'text-emerald-700 dark:text-emerald-300',
  suspicious: 'text-amber-700 dark:text-amber-300',
  scam: 'text-rose-700 dark:text-rose-300',
  expired: 'text-slate-500 dark:text-slate-400',
  unknown: 'text-slate-500 dark:text-slate-400',
};
