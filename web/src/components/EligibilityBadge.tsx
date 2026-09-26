import { cx } from '../lib/cx';
import { ELIGIBILITY_HELP, ELIGIBILITY_STYLES } from '../lib/eligibility';
import { ELIGIBILITY_LABELS } from '../lib/format';
import type { Eligibility } from '../types';
import { BlockIcon, CheckIcon, QuestionIcon } from './icons';

const ICONS: Record<Eligibility, typeof CheckIcon> = {
  eligible: CheckIcon,
  unknown: QuestionIcon,
  ineligible: BlockIcon,
};

interface Props {
  eligibility: Eligibility;
  /** True when the value came from the user's override rather than the rules. */
  overridden?: boolean;
  size?: 'sm' | 'md';
  className?: string;
}

/**
 * Icon + label + colour, always all three. Colour alone would make the three
 * states indistinguishable to a colourblind user, and the distinction between
 * `unknown` and the other two is the whole point of the screen (ADR-0002).
 */
export function EligibilityBadge({
  eligibility,
  overridden = false,
  size = 'sm',
  className,
}: Props) {
  const Icon = ICONS[eligibility];
  const styles = ELIGIBILITY_STYLES[eligibility];

  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-full font-medium ring-1 ring-inset whitespace-nowrap',
        size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-2.5 py-1 text-sm',
        styles.badge,
        className,
      )}
      title={
        overridden
          ? 'Set by you, overriding the rules.'
          : ELIGIBILITY_HELP[eligibility]
      }
    >
      <Icon />
      {ELIGIBILITY_LABELS[eligibility]}
      {overridden && (
        <span
          className="rounded-sm bg-black/10 px-1 text-[10px] font-semibold uppercase tracking-wide dark:bg-white/15"
          title="You set this value yourself."
        >
          yours
        </span>
      )}
    </span>
  );
}
