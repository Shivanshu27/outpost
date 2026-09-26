import { cx } from '../lib/cx';
import { VERIFICATION_STYLES } from '../lib/eligibility';
import { VERIFICATION_LABELS } from '../lib/format';
import type { VerificationOut } from '../types';

interface Props {
  verification: VerificationOut | null;
  className?: string;
}

export function VerificationBadge({ verification, className }: Props) {
  if (!verification) {
    return (
      <span className={cx('text-xs text-slate-400 dark:text-slate-500', className)}>
        Unchecked
      </span>
    );
  }

  const { tier, reasons } = verification;
  return (
    <span
      className={cx('text-xs font-medium', VERIFICATION_STYLES[tier], className)}
      title={reasons.length ? reasons.join('; ') : undefined}
    >
      {VERIFICATION_LABELS[tier]}
    </span>
  );
}
