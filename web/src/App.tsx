import { useCallback, useMemo, useState } from 'react';
import type { SortingState } from '@tanstack/react-table';

import { useJobs, useMeta } from './api/queries';
import { DetailPanel } from './components/DetailPanel';
import { FilterBar } from './components/FilterBar';
import { Header } from './components/Header';
import { JobTable } from './components/JobTable';
import { EmptyState, ErrorState, LoadingState } from './components/States';
import { useDebouncedValue } from './hooks/useDebouncedValue';
import { useTheme } from './hooks/useTheme';
import { cx } from './lib/cx';
import type { JobsQuery } from './types';

/**
 * `eligible` *and* `unknown` — not `eligible` alone.
 *
 * This default is the product guarantee expressed as a constant (ADR-0002):
 * the user may opt into hiding `unknown`, but we never opt them in. A listing
 * we could not parse is exactly the kind a keyword filter would have dropped
 * silently, which is the failure this tool exists to prevent.
 */
const DEFAULT_QUERY: JobsQuery = {
  limit: 100,
  offset: 0,
  eligibility: ['eligible', 'unknown'],
  status: [],
  source: [],
  order_by: 'match_score',
};

export function App() {
  const { preference, setPreference } = useTheme();
  const [query, setQuery] = useState<JobsQuery>(DEFAULT_QUERY);
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([]);

  const debouncedSearch = useDebouncedValue(search, 150);
  const meta = useMeta();
  const jobs = useJobs(query);

  const onQueryChange = useCallback((next: Partial<JobsQuery>) => {
    setQuery((current) => ({ ...current, ...next }));
  }, []);

  const items = useMemo(() => jobs.data?.items ?? [], [jobs.data]);

  // Client-side, over the loaded page only. Cheap, instant, and honest about
  // its scope — the count in the filter bar says "n of m".
  const visible = useMemo(() => {
    const needle = debouncedSearch.trim().toLowerCase();
    if (!needle) return items;
    return items.filter((job) =>
      [job.title, job.company ?? '', job.source, ...job.tags]
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [items, debouncedSearch]);

  const total = jobs.data?.total ?? 0;
  const pageStart = query.offset;
  const pageEnd = query.offset + items.length;
  const hasFilters =
    query.eligibility.length > 0 ||
    query.status.length > 0 ||
    query.source.length > 0 ||
    debouncedSearch.trim() !== '';

  return (
    <div className="flex h-screen flex-col">
      <Header
        meta={meta.data}
        themePreference={preference}
        onThemeChange={setPreference}
      />
      <FilterBar
        query={query}
        onQueryChange={onQueryChange}
        sources={meta.data?.sources ?? []}
        search={search}
        onSearchChange={setSearch}
        loadedCount={items.length}
        filteredCount={visible.length}
      />

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-auto">
          {jobs.isError ? (
            <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />
          ) : jobs.isPending ? (
            <LoadingState />
          ) : visible.length === 0 ? (
            <EmptyState filtered={hasFilters && total > 0} />
          ) : (
            <>
              <JobTable
                jobs={visible}
                selectedId={selectedId}
                onSelect={setSelectedId}
                sorting={sorting}
                onSortingChange={setSorting}
                llmProvider={meta.data?.llm_provider}
              />
              <Pagination
                start={pageStart}
                end={pageEnd}
                total={total}
                limit={query.limit}
                busy={jobs.isFetching}
                onOffsetChange={(offset) => {
                  onQueryChange({ offset });
                  setSelectedId(null);
                }}
              />
            </>
          )}
        </main>

        {selectedId !== null && (
          <>
            {/* Below lg the panel is a drawer over the table; the backdrop is
                only rendered at those widths so it never blocks the split
                layout on a desktop. */}
            <div
              className="fixed inset-0 z-30 bg-slate-900/20 lg:hidden"
              onClick={() => setSelectedId(null)}
              aria-hidden="true"
            />
            <div
              className={cx(
                'fixed inset-y-0 right-0 z-40 w-full max-w-md shadow-2xl',
                'lg:static lg:z-auto lg:w-[26rem] lg:max-w-none lg:shadow-none xl:w-[32rem]',
              )}
            >
              <DetailPanel
                jobId={selectedId}
                onClose={() => setSelectedId(null)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Pagination({
  start,
  end,
  total,
  limit,
  busy,
  onOffsetChange,
}: {
  start: number;
  end: number;
  total: number;
  limit: number;
  busy: boolean;
  onOffsetChange: (offset: number) => void;
}) {
  if (total <= limit && start === 0) return null;

  return (
    <div className="flex items-center gap-3 px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
      <span className="tabular-nums">
        {start + 1}–{end} of {total}
      </span>
      <button
        type="button"
        className="btn"
        disabled={start === 0 || busy}
        onClick={() => onOffsetChange(Math.max(0, start - limit))}
      >
        Previous
      </button>
      <button
        type="button"
        className="btn"
        disabled={end >= total || busy}
        onClick={() => onOffsetChange(start + limit)}
      >
        Next
      </button>
    </div>
  );
}
