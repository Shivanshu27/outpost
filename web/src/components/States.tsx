import { ApiError, NetworkError } from '../api/client';

function Panel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-lg px-6 py-20 text-center">{children}</div>
  );
}

export function TableSkeleton({ rows = 12 }: { rows?: number }) {
  return (
    <div className="px-4 py-3" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          className="mb-1.5 h-8 animate-pulse rounded bg-slate-100 dark:bg-slate-800/70"
          style={{ animationDelay: `${index * 40}ms` }}
        />
      ))}
    </div>
  );
}

export function LoadingState() {
  return (
    <>
      <span className="sr-only" role="status">
        Loading jobs
      </span>
      <TableSkeleton />
    </>
  );
}

/**
 * The two failures worth distinguishing: the server is not running (by far the
 * likely one — `outpost ui` starts it), or it answered with a problem.
 */
export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const unreachable = error instanceof NetworkError;
  const detail =
    error instanceof ApiError
      ? error.detail || error.message
      : error instanceof Error
        ? error.message
        : String(error);

  return (
    <Panel>
      <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
        {unreachable ? 'The Outpost API is not responding' : 'That request failed'}
      </h2>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
        {unreachable ? (
          <>
            This page talks to a local server on port 8420. Start it with{' '}
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs dark:bg-slate-800">
              outpost ui
            </code>{' '}
            and reload.
          </>
        ) : (
          detail
        )}
      </p>
      {onRetry && (
        <button type="button" className="btn mt-4" onClick={onRetry}>
          Try again
        </button>
      )}
    </Panel>
  );
}

export function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <Panel>
      <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-200">
        {filtered ? 'Nothing matches these filters' : 'No jobs yet'}
      </h2>
      <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
        {filtered ? (
          'Widen the eligibility or status filters above — in particular, check whether Unknown is switched off.'
        ) : (
          <>
            Run{' '}
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs dark:bg-slate-800">
              outpost run
            </code>{' '}
            to scrape, label and rank listings. Come back here when it finishes.
          </>
        )}
      </p>
    </Panel>
  );
}

export function DetailPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center text-sm text-slate-400 dark:text-slate-500">
      Select a job to see its eligibility evidence.
    </div>
  );
}
