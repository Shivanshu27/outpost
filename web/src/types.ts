/**
 * Wire types.
 *
 * These mirror `src/outpost/api/schemas.py` field for field. They are written
 * by hand for now rather than generated from the OpenAPI document, so a change
 * on the Python side must be reflected here — the mismatch shows up as a
 * runtime `undefined`, not a build error. Keep the two files adjacent in any
 * review that touches the wire contract.
 *
 * Enum values mirror the StrEnums in `src/outpost/domain/models.py`.
 */

/** ADR-0002 — tri-state, and `unknown` is a first-class verdict. */
export type Eligibility = 'eligible' | 'ineligible' | 'unknown';

export const ELIGIBILITY_VALUES: readonly Eligibility[] = [
  'eligible',
  'unknown',
  'ineligible',
];

export type JobStatus =
  | 'new'
  | 'shortlisted'
  | 'applied'
  | 'rejected'
  | 'dismissed';

export const JOB_STATUS_VALUES: readonly JobStatus[] = [
  'new',
  'shortlisted',
  'applied',
  'rejected',
  'dismissed',
];

export type VerificationTier =
  | 'ok'
  | 'suspicious'
  | 'scam'
  | 'expired'
  | 'unknown';

/** The API validates this against `^(match_score|prescore|posted_at|last_seen)$`. */
export type OrderBy = 'match_score' | 'prescore' | 'posted_at' | 'last_seen';

export const ORDER_BY_VALUES: readonly OrderBy[] = [
  'match_score',
  'prescore',
  'posted_at',
  'last_seen',
];

/** Mirrors `DimensionOut`. */
export interface DimensionOut {
  dimension: string;
  eligibility: Eligibility;
  rule_id: string | null;
  evidence: string | null;
  matched_text: string | null;
}

/** Mirrors `VerificationOut`. */
export interface VerificationOut {
  tier: VerificationTier;
  reasons: string[];
  checked_at: string | null;
}

/** Mirrors `MatchOut`. Scores are only comparable within a `provider` (ADR-0006). */
export interface MatchOut {
  score: number;
  reason: string;
  gaps: string[];
  provider: string;
}

/** Mirrors `CompensationOut`. */
export interface CompensationOut {
  minimum: number | null;
  maximum: number | null;
  currency: string | null;
  period: string | null;
}

/** Mirrors `JobOut` — the list shape. */
export interface JobOut {
  id: string;
  source: string;
  url: string;
  title: string;
  company: string | null;
  location_text: string | null;
  contract_type: string;
  compensation: CompensationOut;
  tags: string[];
  posted_at: string | null;
  first_seen_at: string;
  last_seen_at: string;

  /** The *effective* value — the user's override if they set one. */
  eligibility: Eligibility;
  eligibility_from_rules: Eligibility;
  eligibility_override: Eligibility | null;
  eligibility_summary: string;

  verification: VerificationOut | null;
  match: MatchOut | null;
  prescore: number | null;
  status: JobStatus;
  notes: string | null;
}

/** Mirrors `JobDetailOut` — the list shape plus the heavy fields. */
export interface JobDetailOut extends JobOut {
  description: string;
  dimensions: DimensionOut[];
}

/** Mirrors `JobsPage`. */
export interface JobsPage {
  items: JobOut[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Mirrors `JobPatch`. The API sets `extra="forbid"`, so an unknown key here is
 * a 422 rather than a silently ignored request.
 */
export interface JobPatch {
  status?: JobStatus;
  notes?: string | null;
  eligibility_override?: Eligibility | 'clear';
}

/** Mirrors `StatsOut`. */
export interface StatsOut {
  total: number;
  by_eligibility: Record<string, number>;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  scored: number;
  verified: number;
}

/** Mirrors `MetaOut`. */
export interface MetaOut {
  version: string;
  country: string;
  timezone: string;
  /** `"none"` when no provider is configured — the pipeline stops at stage 7. */
  llm_provider: string;
  has_resume: boolean;
  sources: string[];
  rule_count: number;
}

/** Query parameters accepted by `GET /api/jobs`. */
export interface JobsQuery {
  limit: number;
  offset: number;
  eligibility: Eligibility[];
  status: JobStatus[];
  source: string[];
  order_by: OrderBy;
}
