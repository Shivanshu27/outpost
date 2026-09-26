import { cx } from '../lib/cx';
import { ELIGIBILITY_STYLES } from '../lib/eligibility';
import { ELIGIBILITY_LABELS, ORDER_BY_LABELS, STATUS_LABELS } from '../lib/format';
import {
  ELIGIBILITY_VALUES,
  JOB_STATUS_VALUES,
  ORDER_BY_VALUES,
  type Eligibility,
  type JobStatus,
  type JobsQuery,
  type OrderBy,
} from '../types';

interface Props {
  query: JobsQuery;
  onQueryChange: (next: Partial<JobsQuery>) => void;
  sources: string[];
  search: string;
  onSearchChange: (next: string) => void;
  loadedCount: number;
  filteredCount: number;
}

/** Toggle one member of a multi-select set, preserving declaration order. */
function toggle<T>(values: readonly T[], all: readonly T[], value: T): T[] {
  const next = new Set(values);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return all.filter((candidate) => next.has(candidate));
}

function Chip({
  active,
  onClick,
  children,
  className,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        'rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset transition-colors',
        active
          ? (className ??
            'bg-slate-800 text-white ring-slate-800 dark:bg-slate-200 dark:text-slate-900 dark:ring-slate-200')
          : 'bg-transparent text-slate-500 ring-slate-300 hover:text-slate-800 dark:text-slate-400 dark:ring-slate-700 dark:hover:text-slate-200',
      )}
    >
      {children}
    </button>
  );
}

function Group({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="flex items-center gap-1.5">
      <legend className="sr-only">{label}</legend>
      <span
        aria-hidden="true"
        className="mr-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500"
      >
        {label}
      </span>
      {children}
    </fieldset>
  );
}

export function FilterBar({
  query,
  onQueryChange,
  sources,
  search,
  onSearchChange,
  loadedCount,
  filteredCount,
}: Props) {
  const hidingUnknown = !query.eligibility.includes('unknown');

  return (
    <div className="border-b border-slate-200 bg-slate-50/70 dark:border-slate-800 dark:bg-slate-900/40">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2.5 px-4 py-2.5">
        <Group label="Eligibility">
          {ELIGIBILITY_VALUES.map((value: Eligibility) => (
            <Chip
              key={value}
              active={query.eligibility.includes(value)}
              onClick={() =>
                onQueryChange({
                  eligibility: toggle(
                    query.eligibility,
                    ELIGIBILITY_VALUES,
                    value,
                  ),
                  offset: 0,
                })
              }
              className={cx('ring-1 ring-inset', ELIGIBILITY_STYLES[value].badge)}
            >
              {ELIGIBILITY_LABELS[value]}
            </Chip>
          ))}
        </Group>

        <Group label="Status">
          {JOB_STATUS_VALUES.map((value: JobStatus) => (
            <Chip
              key={value}
              active={query.status.includes(value)}
              onClick={() =>
                onQueryChange({
                  status: toggle(query.status, JOB_STATUS_VALUES, value),
                  offset: 0,
                })
              }
            >
              {STATUS_LABELS[value]}
            </Chip>
          ))}
        </Group>

        {sources.length > 0 && (
          <Group label="Source">
            {sources.map((value) => (
              <Chip
                key={value}
                active={query.source.includes(value)}
                onClick={() =>
                  onQueryChange({
                    source: toggle(query.source, sources, value),
                    offset: 0,
                  })
                }
              >
                {value}
              </Chip>
            ))}
          </Group>
        )}

        <div className="ml-auto flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            Sort
            <select
              className="field"
              value={query.order_by}
              onChange={(event) =>
                onQueryChange({
                  order_by: event.target.value as OrderBy,
                  offset: 0,
                })
              }
            >
              {ORDER_BY_VALUES.map((value) => (
                <option key={value} value={value}>
                  {ORDER_BY_LABELS[value]}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <span className="sr-only sm:not-sr-only">Find</span>
            <input
              type="search"
              className="field w-44"
              placeholder="Filter this page…"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              aria-label="Filter the loaded jobs by title, company or tag"
            />
          </label>

          <span className="whitespace-nowrap text-xs tabular-nums text-slate-400 dark:text-slate-500">
            {filteredCount === loadedCount
              ? `${loadedCount} shown`
              : `${filteredCount} of ${loadedCount}`}
          </span>
        </div>
      </div>

      {/*
        The one nudge the product allows itself, and it points the other way.
        ADR-0002: the user may opt into hiding `unknown`, but they should know
        what it costs, because the jobs it hides are exactly the ones no other
        tool would have surfaced either.
      */}
      {hidingUnknown && (
        <p className="border-t border-slate-200/70 px-4 py-1.5 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
          Jobs whose eligibility could not be determined are hidden. Those are
          listings Outpost could not read confidently — some of them you can
          take.{' '}
          <button
            type="button"
            className="font-medium text-sky-700 underline underline-offset-2 dark:text-sky-400"
            onClick={() =>
              onQueryChange({
                eligibility: toggle(
                  query.eligibility,
                  ELIGIBILITY_VALUES,
                  'unknown',
                ),
                offset: 0,
              })
            }
          >
            Show them again
          </button>
        </p>
      )}
    </div>
  );
}
