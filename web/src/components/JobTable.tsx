import { useMemo, useRef } from 'react';
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table';

import { cx } from '../lib/cx';
import { formatDate, relativeDays, titleCase } from '../lib/format';
import type { JobOut } from '../types';
import { EligibilityBadge } from './EligibilityBadge';
import { ScoreCell } from './ScoreCell';
import { VerificationBadge } from './VerificationBadge';

interface Props {
  jobs: JobOut[];
  selectedId: string | null;
  onSelect: (jobId: string) => void;
  sorting: SortingState;
  onSortingChange: (next: SortingState) => void;
  llmProvider: string | undefined;
}

const columnHelper = createColumnHelper<JobOut>();

export function JobTable({
  jobs,
  selectedId,
  onSelect,
  sorting,
  onSortingChange,
  llmProvider,
}: Props) {
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  const columns = useMemo(
    () => [
      columnHelper.accessor((job) => job.match?.score ?? job.prescore ?? -1, {
        id: 'score',
        header: 'Score',
        size: 64,
        cell: (ctx) => (
          <ScoreCell
            match={ctx.row.original.match}
            prescore={ctx.row.original.prescore}
            llmProvider={llmProvider}
          />
        ),
      }),
      columnHelper.accessor('title', {
        header: 'Title',
        cell: (ctx) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-slate-900 dark:text-slate-100">
              {ctx.getValue()}
            </div>
            {/* The summary is the reason for the verdict; it belongs next to
                the title, not buried in the panel. */}
            <div className="truncate text-xs text-slate-500 dark:text-slate-400">
              {ctx.row.original.eligibility_summary}
            </div>
          </div>
        ),
      }),
      columnHelper.accessor((job) => job.company ?? '', {
        id: 'company',
        header: 'Company',
        cell: (ctx) => (
          <span className="block truncate text-slate-700 dark:text-slate-300">
            {ctx.getValue() || '—'}
          </span>
        ),
      }),
      columnHelper.accessor('eligibility', {
        header: 'Eligibility',
        enableSorting: false,
        cell: (ctx) => (
          <EligibilityBadge
            eligibility={ctx.getValue()}
            overridden={ctx.row.original.eligibility_override !== null}
          />
        ),
      }),
      columnHelper.accessor((job) => job.verification?.tier ?? '', {
        id: 'verification',
        header: 'Verified',
        enableSorting: false,
        cell: (ctx) => (
          <VerificationBadge verification={ctx.row.original.verification} />
        ),
      }),
      columnHelper.accessor('source', {
        header: 'Source',
        cell: (ctx) => (
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {titleCase(ctx.getValue())}
          </span>
        ),
      }),
      columnHelper.accessor((job) => job.posted_at ?? '', {
        id: 'posted_at',
        header: 'Posted',
        cell: (ctx) => (
          <span
            className="whitespace-nowrap text-xs text-slate-500 dark:text-slate-400"
            title={formatDate(ctx.row.original.posted_at)}
          >
            {relativeDays(ctx.row.original.posted_at) ?? '—'}
          </span>
        ),
      }),
    ],
    [llmProvider],
  );

  const table = useReactTable({
    data: jobs,
    columns,
    state: { sorting },
    onSortingChange: (updater) => {
      onSortingChange(
        typeof updater === 'function' ? updater(sorting) : updater,
      );
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: (job) => job.id,
  });

  const rows = table.getRowModel().rows;

  /** Roving focus: the table is meant to be skimmed without the mouse. */
  function onRowKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>) {
    const { key } = event;
    if (key !== 'ArrowDown' && key !== 'ArrowUp' && key !== 'Enter' && key !== ' ') {
      return;
    }
    event.preventDefault();

    const current = event.currentTarget;
    if (key === 'Enter' || key === ' ') {
      onSelect(current.dataset['jobId'] as string);
      return;
    }

    const all = Array.from(
      bodyRef.current?.querySelectorAll<HTMLTableRowElement>('tr[data-job-id]') ??
        [],
    );
    const index = all.indexOf(current);
    const next = all[key === 'ArrowDown' ? index + 1 : index - 1];
    next?.focus();
  }

  return (
    <table className="w-full border-collapse text-sm">
      <caption className="sr-only">
        Job listings. Use the arrow keys to move between rows and Enter to open
        one.
      </caption>
      <thead className="sticky top-0 z-10 bg-white/95 backdrop-blur dark:bg-slate-950/95">
        {table.getHeaderGroups().map((headerGroup) => (
          <tr
            key={headerGroup.id}
            className="border-b border-slate-200 dark:border-slate-800"
          >
            {headerGroup.headers.map((header) => {
              const sortable = header.column.getCanSort();
              const direction = header.column.getIsSorted();
              return (
                <th
                  key={header.id}
                  scope="col"
                  aria-sort={
                    !sortable || !direction
                      ? undefined
                      : direction === 'asc'
                        ? 'ascending'
                        : 'descending'
                  }
                  className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400"
                  style={header.column.id === 'score' ? { width: 72 } : undefined}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className="inline-flex items-center gap-1 rounded hover:text-slate-800 dark:hover:text-slate-200"
                    >
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                      <span aria-hidden="true" className="text-[9px]">
                        {direction === 'asc' ? '▲' : direction === 'desc' ? '▼' : '↕'}
                      </span>
                    </button>
                  ) : (
                    flexRender(
                      header.column.columnDef.header,
                      header.getContext(),
                    )
                  )}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>

      <tbody ref={bodyRef}>
        {rows.map((row) => {
          const selected = row.original.id === selectedId;
          return (
            <tr
              key={row.id}
              data-job-id={row.original.id}
              tabIndex={0}
              aria-selected={selected}
              onClick={() => onSelect(row.original.id)}
              onKeyDown={onRowKeyDown}
              className={cx(
                'cursor-pointer border-b border-slate-100 dark:border-slate-800/70',
                'hover:bg-slate-50 dark:hover:bg-slate-900',
                selected && 'bg-sky-50 dark:bg-sky-500/10',
                // Status is context, not a column: dismissed and rejected rows
                // stay legible but recede.
                (row.original.status === 'dismissed' ||
                  row.original.status === 'rejected') &&
                  'text-slate-400 dark:text-slate-500',
              )}
            >
              {row.getVisibleCells().map((cell) => (
                <td
                  key={cell.id}
                  className={cx(
                    'px-3 py-1.5 align-middle',
                    cell.column.id === 'title' && 'max-w-0 w-[38%]',
                    cell.column.id === 'company' && 'max-w-0 w-[18%]',
                  )}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
