import { useEffect, useRef } from 'react';

import { useJob } from '../api/queries';
import {
  formatCompensation,
  formatDate,
  formatDateTime,
  titleCase,
  VERIFICATION_LABELS,
} from '../lib/format';
import { DimensionList } from './DimensionList';
import { EligibilityBadge } from './EligibilityBadge';
import { JobActions } from './JobActions';
import { CloseIcon, ExternalLinkIcon } from './icons';
import { DetailPlaceholder, ErrorState } from './States';
import type { JobDetailOut } from '../types';

interface Props {
  jobId: string | null;
  onClose: () => void;
}

function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-slate-200 px-4 py-4 dark:border-slate-800">
      <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function DetailPanel({ jobId, onClose }: Props) {
  const { data: job, error, isLoading, refetch } = useJob(jobId);
  const panelRef = useRef<HTMLDivElement>(null);

  // Escape closes, and opening moves focus into the panel so a keyboard user
  // is not left behind in the table.
  useEffect(() => {
    if (!jobId) return;
    panelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [jobId, onClose]);

  return (
    <aside
      ref={panelRef}
      tabIndex={-1}
      aria-label="Job detail"
      className="flex h-full flex-col overflow-y-auto border-l border-slate-200 bg-slate-50/60 dark:border-slate-800 dark:bg-slate-900/30"
    >
      {jobId === null ? (
        <DetailPlaceholder />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading || !job ? (
        <p className="p-6 text-sm text-slate-400" role="status">
          Loading…
        </p>
      ) : (
        <DetailBody job={job} onClose={onClose} />
      )}
    </aside>
  );
}

function DetailBody({
  job,
  onClose,
}: {
  job: JobDetailOut;
  onClose: () => void;
}) {
  const pay = formatCompensation(job.compensation);

  return (
    <>
      <div className="sticky top-0 z-10 bg-slate-50/95 px-4 py-3 backdrop-blur dark:bg-slate-900/80">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold leading-snug">{job.title}</h2>
            <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">
              {job.company ?? 'Unknown company'}
              {job.location_text && (
                <span className="text-slate-400 dark:text-slate-500">
                  {' · '}
                  {job.location_text}
                </span>
              )}
            </p>
          </div>
          <button
            type="button"
            className="btn !px-1.5"
            onClick={onClose}
            aria-label="Close detail panel"
          >
            <CloseIcon />
          </button>
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
          <EligibilityBadge
            eligibility={job.eligibility}
            overridden={job.eligibility_override !== null}
            size="md"
          />
          <span>{titleCase(job.source)}</span>
          <span>·</span>
          <span>{titleCase(job.contract_type)}</span>
          {pay && (
            <>
              <span>·</span>
              <span>{pay}</span>
            </>
          )}
          <span>·</span>
          <span>Posted {formatDate(job.posted_at)}</span>
          <a
            href={job.url}
            target="_blank"
            rel="noreferrer noopener"
            className="ml-auto inline-flex items-center gap-1 font-medium text-sky-700 hover:underline dark:text-sky-400"
          >
            Open listing
            <ExternalLinkIcon />
          </a>
        </div>
      </div>

      <Block title="Eligibility">
        <p className="text-sm text-slate-700 dark:text-slate-200">
          {job.eligibility_summary}
        </p>
        {job.eligibility_override !== null && (
          <p className="mt-2 rounded-md bg-sky-50 px-2.5 py-2 text-xs text-sky-900 dark:bg-sky-400/10 dark:text-sky-200">
            You overrode this. The rules said{' '}
            <strong>{job.eligibility_from_rules}</strong>; the list shows your
            verdict.
          </p>
        )}
        <div className="mt-3">
          <DimensionList dimensions={job.dimensions} />
        </div>
      </Block>

      {job.match && (
        <Block title="Match">
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-2xl tabular-nums">
              {job.match.score}
            </span>
            {/* Quiet by design: it matters, but it is a caveat, not a headline. */}
            <span className="text-xs text-slate-400 dark:text-slate-500">
              scored by {job.match.provider} — comparable only against other{' '}
              {job.match.provider} scores
            </span>
          </div>
          <p className="mt-2 text-sm text-slate-700 dark:text-slate-200">
            {job.match.reason}
          </p>
          {job.match.gaps.length > 0 && (
            <>
              <h3 className="mt-3 text-xs font-semibold text-slate-500 dark:text-slate-400">
                Gaps
              </h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm text-slate-600 dark:text-slate-300">
                {job.match.gaps.map((gap) => (
                  <li key={gap}>{gap}</li>
                ))}
              </ul>
            </>
          )}
        </Block>
      )}

      {job.verification && (
        <Block title="Verification">
          <p className="text-sm text-slate-700 dark:text-slate-200">
            {VERIFICATION_LABELS[job.verification.tier]}
            <span className="text-slate-400 dark:text-slate-500">
              {' · checked '}
              {formatDateTime(job.verification.checked_at)}
            </span>
          </p>
          {job.verification.reasons.length > 0 && (
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-sm text-slate-600 dark:text-slate-300">
              {job.verification.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </Block>
      )}

      <Block title="Your call">
        <JobActions job={job} />
      </Block>

      {job.tags.length > 0 && (
        <Block title="Tags">
          <div className="flex flex-wrap gap-1.5">
            {job.tags.map((tag) => (
              <span
                key={tag}
                className="rounded bg-slate-200/70 px-1.5 py-0.5 text-xs text-slate-700 dark:bg-slate-800 dark:text-slate-300"
              >
                {tag}
              </span>
            ))}
          </div>
        </Block>
      )}

      <Block title="Description">
        <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700 dark:text-slate-300">
          {job.description || 'The source returned no description.'}
        </div>
      </Block>

      <p className="px-4 pb-6 pt-2 text-[11px] text-slate-400 dark:text-slate-600">
        First seen {formatDateTime(job.first_seen_at)} · last seen{' '}
        {formatDateTime(job.last_seen_at)}
      </p>
    </>
  );
}
