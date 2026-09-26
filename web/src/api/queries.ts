/**
 * TanStack Query bindings.
 *
 * Server state lives here and nowhere else — there is no separate store
 * (ADR-0010). Mutations are optimistic with rollback: the database is a local
 * SQLite file, so marking twenty jobs dismissed must feel instant.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';

import { getJob, getMeta, getStats, listJobs, patchJob } from './client';
import type {
  JobDetailOut,
  JobOut,
  JobPatch,
  JobsPage,
  JobsQuery,
  MetaOut,
  StatsOut,
} from '../types';

export const queryKeys = {
  jobs: (query: JobsQuery) => ['jobs', query] as const,
  jobsAll: ['jobs'] as const,
  job: (id: string) => ['job', id] as const,
  meta: ['meta'] as const,
  stats: ['stats'] as const,
};

export function useJobs(query: JobsQuery) {
  return useQuery<JobsPage>({
    queryKey: queryKeys.jobs(query),
    queryFn: ({ signal }) => listJobs(query, signal),
    // Filter changes swap the key; keeping the previous page on screen while
    // the next one loads avoids a full-table flash on every checkbox.
    placeholderData: (previous) => previous,
  });
}

export function useJob(jobId: string | null) {
  return useQuery<JobDetailOut>({
    queryKey: queryKeys.job(jobId ?? ''),
    queryFn: ({ signal }) => getJob(jobId as string, signal),
    enabled: jobId !== null,
  });
}

export function useMeta() {
  return useQuery<MetaOut>({
    queryKey: queryKeys.meta,
    queryFn: ({ signal }) => getMeta(signal),
    staleTime: 5 * 60 * 1000,
  });
}

export function useStats() {
  return useQuery<StatsOut>({
    queryKey: queryKeys.stats,
    queryFn: ({ signal }) => getStats(signal),
  });
}

/**
 * Apply a patch to a cached job the way the server would.
 *
 * The one piece of real logic: `eligibility` is the *effective* value, so an
 * override has to be folded into it locally or the optimistic row disagrees
 * with the badge it just rendered.
 */
function applyPatch<T extends JobOut>(job: T, patch: JobPatch): T {
  const next: T = { ...job };
  if (patch.status !== undefined) next.status = patch.status;
  if (patch.notes !== undefined) next.notes = patch.notes || null;
  if (patch.eligibility_override !== undefined) {
    const override =
      patch.eligibility_override === 'clear' ? null : patch.eligibility_override;
    next.eligibility_override = override;
    next.eligibility = override ?? job.eligibility_from_rules;
  }
  return next;
}

/** Write the optimistic value into every cached list page and the detail entry. */
function writeOptimistic(
  client: QueryClient,
  jobId: string,
  patch: JobPatch,
): void {
  client.setQueriesData<JobsPage>(
    { queryKey: queryKeys.jobsAll },
    (page) =>
      page && {
        ...page,
        items: page.items.map((item) =>
          item.id === jobId ? applyPatch(item, patch) : item,
        ),
      },
  );
  client.setQueryData<JobDetailOut>(
    queryKeys.job(jobId),
    (detail) => detail && applyPatch(detail, patch),
  );
}

interface PatchVariables {
  jobId: string;
  patch: JobPatch;
}

interface PatchContext {
  /** Snapshot of everything touched, so a failure restores exactly what was there. */
  previous: [readonly unknown[], unknown][];
}

export function usePatchJob() {
  const client = useQueryClient();

  return useMutation<JobDetailOut, Error, PatchVariables, PatchContext>({
    mutationFn: ({ jobId, patch }) => patchJob(jobId, patch),
    onMutate: async ({ jobId, patch }) => {
      // In-flight reads would otherwise land after the optimistic write and
      // overwrite it with pre-patch data.
      await client.cancelQueries({ queryKey: queryKeys.jobsAll });
      await client.cancelQueries({ queryKey: queryKeys.job(jobId) });

      const previous: [readonly unknown[], unknown][] = [
        ...client
          .getQueriesData<JobsPage>({ queryKey: queryKeys.jobsAll })
          .map(([key, data]): [readonly unknown[], unknown] => [key, data]),
        [queryKeys.job(jobId), client.getQueryData(queryKeys.job(jobId))],
      ];

      writeOptimistic(client, jobId, patch);
      return { previous };
    },
    onError: (_error, _variables, context) => {
      for (const [key, data] of context?.previous ?? []) {
        client.setQueryData(key, data);
      }
    },
    onSuccess: (job) => {
      // The server is authoritative about derived fields; take its answer.
      client.setQueryData(queryKeys.job(job.id), job);
      client.setQueriesData<JobsPage>(
        { queryKey: queryKeys.jobsAll },
        (page) =>
          page && {
            ...page,
            items: page.items.map((item) =>
              item.id === job.id ? { ...item, ...stripDetail(job) } : item,
            ),
          },
      );
      void client.invalidateQueries({ queryKey: queryKeys.stats });
    },
  });
}

/** The list cache holds `JobOut`; the heavy detail fields do not belong in it. */
function stripDetail(job: JobDetailOut): JobOut {
  const { description: _description, dimensions: _dimensions, ...rest } = job;
  return rest;
}
