import { cx } from '../lib/cx';
import { ELIGIBILITY_STYLES } from '../lib/eligibility';
import { titleCase } from '../lib/format';
import type { DimensionOut } from '../types';
import { EligibilityBadge } from './EligibilityBadge';

/**
 * Every dimension, with its evidence and the exact text that matched.
 *
 * This is the audit trail ADR-0002 promises: a verdict the user cannot check
 * is a bug, and "we found nothing on this axis" is itself a result worth
 * showing — it is how a user discovers that a rule is missing for their
 * country, which is the contribution the project most needs.
 */
export function DimensionList({ dimensions }: { dimensions: DimensionOut[] }) {
  if (dimensions.length === 0) {
    return (
      <p className="text-sm text-slate-500 dark:text-slate-400">
        No dimensions were evaluated for this listing.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {dimensions.map((dimension) => (
        <li
          key={dimension.dimension}
          className={cx(
            'rounded-md border border-l-2 border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/40',
            ELIGIBILITY_STYLES[dimension.eligibility].accent,
          )}
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-slate-800 dark:text-slate-200">
              {titleCase(dimension.dimension)}
            </span>
            <EligibilityBadge eligibility={dimension.eligibility} />
            {dimension.rule_id && (
              <code className="ml-auto font-mono text-[11px] text-slate-400 dark:text-slate-500">
                {dimension.rule_id}
              </code>
            )}
          </div>

          {dimension.evidence ? (
            <p className="mt-1.5 text-sm text-slate-600 dark:text-slate-300">
              {dimension.evidence}
            </p>
          ) : (
            <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
              No rule matched on this axis, so the listing states no constraint
              we recognise here.
            </p>
          )}

          {dimension.matched_text && (
            <blockquote className="mt-2 border-l-2 border-slate-300 pl-2.5 font-mono text-xs leading-relaxed text-slate-600 dark:border-slate-600 dark:text-slate-400">
              &ldquo;{dimension.matched_text}&rdquo;
            </blockquote>
          )}
        </li>
      ))}
    </ul>
  );
}
