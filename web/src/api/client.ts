/**
 * Typed fetch wrappers over the local API.
 *
 * Every call the UI makes goes through an endpoint the CLI could equally call
 * (ADR-0010) — there are no privileged routes here, and nothing is invented
 * that `api/app.py` does not serve.
 */

import type {
  JobDetailOut,
  JobPatch,
  JobsPage,
  JobsQuery,
  MetaOut,
  StatsOut,
} from '../types';

/**
 * Relative so the same bundle works behind the Vite dev proxy and when served
 * by FastAPI from `resources/web`.
 */
const BASE = 'api';

/** A failed request, carrying whatever the server was willing to explain. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | null;

  constructor(message: string, status: number, detail: string | null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

/** Thrown when the API could not be reached at all — usually `outpost ui` is not running. */
export class NetworkError extends Error {
  constructor(cause: unknown) {
    super('Could not reach the Outpost API.');
    this.name = 'NetworkError';
    this.cause = cause;
  }
}

/**
 * FastAPI returns `{"detail": ...}` for HTTPException and a list of objects
 * for a 422. Both are worth showing; neither is worth crashing over.
 */
async function readDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
      if (detail !== undefined) return JSON.stringify(detail);
    }
    return null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
      ...init,
    });
  } catch (cause) {
    throw new NetworkError(cause);
  }

  if (!response.ok) {
    const detail = await readDetail(response);
    throw new ApiError(
      detail ?? `Request failed with ${response.status}`,
      response.status,
      detail,
    );
  }

  return (await response.json()) as T;
}

/**
 * Repeated keys, not comma-joined: FastAPI's `list[...] = Query()` binds
 * `?eligibility=eligible&eligibility=unknown`, and a comma-joined value would
 * arrive as one nonsense enum member.
 */
export function jobsSearchParams(query: JobsQuery): URLSearchParams {
  const params = new URLSearchParams();
  params.set('limit', String(query.limit));
  params.set('offset', String(query.offset));
  params.set('order_by', query.order_by);
  for (const value of query.eligibility) params.append('eligibility', value);
  for (const value of query.status) params.append('status', value);
  for (const value of query.source) params.append('source', value);
  return params;
}

export function listJobs(
  query: JobsQuery,
  signal?: AbortSignal,
): Promise<JobsPage> {
  return request<JobsPage>(`/jobs?${jobsSearchParams(query).toString()}`, {
    signal,
  });
}

export function getJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<JobDetailOut> {
  return request<JobDetailOut>(`/jobs/${encodeURIComponent(jobId)}`, { signal });
}

export function patchJob(
  jobId: string,
  patch: JobPatch,
): Promise<JobDetailOut> {
  return request<JobDetailOut>(`/jobs/${encodeURIComponent(jobId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export function getMeta(signal?: AbortSignal): Promise<MetaOut> {
  return request<MetaOut>('/meta', { signal });
}

export function getStats(signal?: AbortSignal): Promise<StatsOut> {
  return request<StatsOut>('/stats', { signal });
}
