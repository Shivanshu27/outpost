import { useEffect, useRef, useState } from 'react';

import { usePatchJob } from '../api/queries';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { cx } from '../lib/cx';
import { ELIGIBILITY_STYLES } from '../lib/eligibility';
import { ELIGIBILITY_LABELS, STATUS_LABELS } from '../lib/format';
import {
  ELIGIBILITY_VALUES,
  JOB_STATUS_VALUES,
  type Eligibility,
  type JobDetailOut,
  type JobStatus,
} from '../types';

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {title}
      </h3>
      {hint && (
        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{hint}</p>
      )}
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

export function JobActions({ job }: { job: JobDetailOut }) {
  const patch = usePatchJob();

  return (
    <div className="space-y-4">
      <Section title="Status">
        <select
          className="field w-full"
          aria-label="Job status"
          value={job.status}
          onChange={(event) =>
            patch.mutate({
              jobId: job.id,
              patch: { status: event.target.value as JobStatus },
            })
          }
        >
          {JOB_STATUS_VALUES.map((value) => (
            <option key={value} value={value}>
              {STATUS_LABELS[value]}
            </option>
          ))}
        </select>
      </Section>

      <Section
        title="Your eligibility verdict"
        hint="Overrides the rules for this listing only. Yours is what the list shows."
      >
        <div className="flex flex-wrap items-center gap-1.5">
          {ELIGIBILITY_VALUES.map((value: Eligibility) => {
            const active = job.eligibility_override === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  patch.mutate({
                    jobId: job.id,
                    patch: { eligibility_override: active ? 'clear' : value },
                  })
                }
                className={cx(
                  'rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset transition-colors',
                  active
                    ? ELIGIBILITY_STYLES[value].badge
                    : 'text-slate-500 ring-slate-300 hover:text-slate-800 dark:text-slate-400 dark:ring-slate-700 dark:hover:text-slate-200',
                )}
              >
                {ELIGIBILITY_LABELS[value]}
              </button>
            );
          })}
          {job.eligibility_override !== null && (
            <button
              type="button"
              className="ml-1 text-xs text-slate-500 underline underline-offset-2 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
              onClick={() =>
                patch.mutate({
                  jobId: job.id,
                  patch: { eligibility_override: 'clear' },
                })
              }
            >
              Use the rules&rsquo; verdict (
              {ELIGIBILITY_LABELS[job.eligibility_from_rules]})
            </button>
          )}
        </div>
      </Section>

      <Section title="Notes">
        <NotesEditor job={job} />
      </Section>

      {patch.isError && (
        <p
          role="alert"
          className="text-xs text-rose-700 dark:text-rose-300"
        >
          That change did not save and has been rolled back.{' '}
          {patch.error instanceof Error ? patch.error.message : ''}
        </p>
      )}
    </div>
  );
}

/**
 * Debounced autosave.
 *
 * The draft is keyed by job id so switching jobs never carries one listing's
 * half-typed note onto another, and the effect below deliberately does not
 * sync from the server value on every render — that would fight the user's
 * cursor while the save is in flight.
 */
function NotesEditor({ job }: { job: JobDetailOut }) {
  const patch = usePatchJob();
  const [draft, setDraft] = useState(job.notes ?? '');
  const debounced = useDebouncedValue(draft, 600);
  const lastSaved = useRef(job.notes ?? '');
  const jobId = useRef(job.id);

  useEffect(() => {
    if (jobId.current !== job.id) {
      jobId.current = job.id;
      lastSaved.current = job.notes ?? '';
      setDraft(job.notes ?? '');
    }
  }, [job.id, job.notes]);

  useEffect(() => {
    if (jobId.current !== job.id) return;
    if (debounced === lastSaved.current) return;
    lastSaved.current = debounced;
    patch.mutate({ jobId: job.id, patch: { notes: debounced } });
    // `patch` is a stable mutation object from TanStack Query; including it
    // would re-fire the save on every mutation state transition.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced, job.id]);

  const dirty = draft !== lastSaved.current;

  return (
    <div>
      <textarea
        className="field h-24 w-full resize-y"
        aria-label="Notes"
        placeholder="Why this one, what you sent, who replied…"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
      />
      <p
        className="mt-1 h-4 text-[11px] text-slate-400 dark:text-slate-500"
        aria-live="polite"
      >
        {dirty ? 'Saving…' : draft ? 'Saved' : ''}
      </p>
    </div>
  );
}
