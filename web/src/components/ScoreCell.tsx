import { cx } from '../lib/cx';
import type { MatchOut } from '../types';

interface Props {
  match: MatchOut | null;
  prescore: number | null;
  /** From `GET /api/meta`; `"none"` means nothing was scored by an LLM. */
  llmProvider: string | undefined;
}

/**
 * A blank score with no reason reads as a bug, so an unscored job says which
 * kind of nothing it is: no provider configured, or ranked but not yet scored.
 */
export function ScoreCell({ match, prescore, llmProvider }: Props) {
  if (match) {
    return (
      <span
        className={cx(
          'inline-block min-w-[2.25rem] rounded px-1.5 py-0.5 text-right font-mono text-sm tabular-nums',
          match.score >= 70
            ? 'bg-sky-50 font-semibold text-sky-800 dark:bg-sky-400/10 dark:text-sky-200'
            : 'text-slate-700 dark:text-slate-300',
        )}
        title={`Scored by ${match.provider}. Scores are only comparable within a provider.`}
      >
        {match.score}
      </span>
    );
  }

  if (prescore !== null) {
    return (
      <span
        className="font-mono text-sm tabular-nums text-slate-400 dark:text-slate-500"
        title="Heuristic pre-score, not an LLM match score."
      >
        {/* prescore is a 0–1 float; match.score above is 0–100. Rounding the
            raw value collapses every row to ~0 or ~1, so scale it onto the
            same 0–100 axis the reader is already comparing against. */}
        ~{Math.round(prescore * 100)}
      </span>
    );
  }

  return (
    <span
      className="text-xs text-slate-400 dark:text-slate-500"
      title={
        llmProvider === 'none'
          ? 'No scoring provider is configured, so nothing was scored.'
          : 'Not scored in the last run.'
      }
    >
      —
    </span>
  );
}
