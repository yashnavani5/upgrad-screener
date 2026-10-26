// zod validation at the database boundary. Each schema's output must be assignable to the generated
// contract type (see the checks at the bottom), so a contract change that is not mirrored here fails `tsc`.
import { z } from "zod";

import type {
  ActivityRow, CompanyDetailRow, FunnelRow, LeftOutRow, LongListRow, RunRow, SearchRow, ShortlistRow, UniverseRow,
} from "./contracts";

// Fit categories come from the AI Policy (default acquire / partner / co_build); ids are snake_case.
const fitType = z.string().regex(/^[a-z][a-z0-9_]{1,19}$/);
const reasonCode = z.enum([
  "TOO_LARGE", "COMPETITOR", "UPGRAD_OWNED", "UNVERIFIABLE", "INACTIVE", "LOW_FIT", "NOT_EDUCATION", "DUPLICATE_THESIS", "ACQUIRED", "POLICY_FILTER",
]);
const region = z.enum(["india", "outside", "unknown"]).default("unknown");
const num = z.number();
const unit = z.number().min(0).max(1);

export const founderRef = z.object({
  name: z.string(),
  linkedin_url: z.string().nullable().optional(),
  role: z.string().nullable().optional(),
});
// Verified founder background (prior roles, exits, education) and the one-line bio from the explain stage.
export const founderFact = z.object({
  field: z.enum(["prior_role", "prior_exit", "education"]), value: z.record(z.string(), z.unknown()), source_url: z.string(),
});
const profileFields = {
  title: z.string().nullable().optional(), identity_verified: z.boolean().default(false),
  x_url: z.string().nullable().optional(), background: z.array(founderFact).default([]), line: z.string().nullable().optional(),
  why: z.string().nullable().optional(), fit: z.string().nullable().optional(),
};
export const founderProfile = founderRef.extend({ founder_id: z.string().nullable().optional(), ...profileFields });
export const evidenceRef = z.object({ url: z.string(), title: z.string().nullable().optional(), domain: z.string() }).loose();

export const shortlistRow = z.object({
  run_id: z.string(), is_latest: z.boolean(), rank: z.number().int(), entity_id: z.string(), company: z.string(),
  website: z.string().nullable(), founders: z.array(founderProfile), fit_type: fitType, final: unit, t: unit, i: unit,
  f: unit, confidence: unit, why_line: z.string(), fit_line: z.string(), evidence: z.array(evidenceRef),
  last_verified: z.string().nullable(), reviewer_label: z.string().nullable(),
  region, region_basis: z.string().default("no location evidence"), size_verified: z.boolean().default(false),
  description: z.string().nullable().optional(),
  reviewer_note: z.string().nullable().optional(), reviewer: z.string().nullable().optional(),
});

export const leftOutRow = z.object({
  run_id: z.string(), is_latest: z.boolean(), entity_id: z.string(), company: z.string(), founders: z.array(founderRef),
  reason_code: reasonCode, explanation: z.string(), would_have_ranked: z.number().int().nullable(),
  source_url: z.string().nullable(), region,
  featured: z.number().int().nullable().optional(), featured_category: z.enum(["big", "evidence", "close"]).nullable().optional(),
  featured_line: z.string().nullable().optional(), description: z.string().nullable().optional(),
});

export const longListRow = z.object({
  run_id: z.string(), is_latest: z.boolean(), rank: z.number().int(), entity_id: z.string(), company: z.string(),
  website: z.string().nullable(), founders: z.array(founderRef), fit_type: fitType.nullable(), final: unit,
  t: unit.nullable(), m: unit.nullable(), n: unit.nullable(), i: unit.nullable(), f: unit.nullable(),
  f_acquire: unit.nullable(), f_partner: unit.nullable(), f_co_build: unit.nullable(), confidence: unit,
  bt_pct: unit.nullable(), gate_status: z.enum(["passed", "failed"]), reason_code: reasonCode.nullable(), region,
});

const scoreComponent = z.object({
  name: z.enum(["T", "M", "N", "I", "F_acquire", "F_partner", "F_co_build", "F", "C", "final", "bt_pct", "final_prime"]),
  raw: num.nullable().optional(), adjusted: num.nullable().optional(), n_signals: z.number().int(),
  pool_mean: num.nullable().optional(), weight: num.nullable().optional(), why_not_higher: z.string().nullable().optional(),
});
const claimRef = z.object({
  field: z.string(), value: z.unknown(), source_url: z.string(), evidence_span: z.string(), retrieved_at: z.string(),
  verifier_confidence: num.nullable().optional(),
});
export const companyDetailRow = z.object({
  run_id: z.string(), is_latest: z.boolean(), entity_id: z.string(), company: z.string(), website: z.string().nullable(),
  description: z.string().nullable(), country: z.string().nullable(), city: z.string().nullable(),
  founded_year: z.number().int().nullable(), status: z.enum(["active", "inactive", "unknown"]),
  total_raised_usd: num.nullable(), headcount: z.number().int().nullable(), fit_type: fitType.nullable(),
  rank: z.number().int().nullable(),
  founders: z.array(z.object({
    founder_id: z.string(), name: z.string(), linkedin_url: z.string().nullable().optional(),
    role: z.string().nullable().optional(), is_current: z.boolean(), ...profileFields,
  })),
  rounds: z.array(z.object({
    round_type: z.string().nullable().optional(), amount_usd: num.nullable().optional(),
    announced_on: z.string().nullable().optional(), investors: z.array(z.string()).default([]), source_url: z.string(),
  })),
  score_breakdown: z.array(scoreComponent),
  claims: z.array(claimRef),
  region, region_basis: z.string().default("no location evidence"),
});

const checkpoint = z.object({
  status: z.string().optional(), summary: z.string().nullable().optional(), started_at: z.string().nullable().optional(),
  finished_at: z.string().nullable().optional(), retry: z.boolean().optional(),
}).loose();
export const runRow = z.object({
  run_id: z.string(), status: z.enum(["queued", "running", "succeeded", "failed", "partial"]),
  current_stage: z.enum(["discover", "resolve", "enrich", "verify", "score", "explain", "publish"]).nullable(),
  stage_progress: unit, trigger: z.enum(["cli", "dashboard", "schedule"]), started_at: z.string(),
  finished_at: z.string().nullable(), heartbeat_at: z.string().nullable(),
  counts: z.object({
    candidates: z.number().default(0), companies: z.number().default(0), founders: z.number().default(0),
    claims_supported: z.number().default(0), shortlisted: z.number().default(0),
  }).loose(),
  provider_usage: z.record(z.string(), z.unknown()), cost_usd: num, error_summary: z.string().nullable(),
  config_snapshot: z.object({
    weights: z.record(z.string(), z.unknown()), gap_map: z.record(z.string(), z.unknown()), exclusions: z.record(z.string(), z.unknown()),
    models: z.record(z.string(), z.string()),
  }).nullable(),
  sheet_url: z.string().nullable(), is_latest: z.boolean(),
  checkpoints: z.record(z.string(), checkpoint).default({}), options: z.record(z.string(), z.unknown()).default({}),
});

export const funnelRow = z.object({
  run_id: z.string(), is_latest: z.boolean(),
  stage: z.enum(["discovered", "resolved", "enriched", "verified", "gated", "scored", "shortlisted"]),
  stage_order: z.number().int(), count: z.number().int(),
});
export const activityRow = z.object({
  provider: z.string(), calls_5m: z.coerce.number().int(), errors_5m: z.coerce.number().int(), calls_1h: z.coerce.number().int(),
  median_latency_ms_1h: z.number().int().nullable(), last_call_at: z.string().nullable(),
});
export const searchRow = z.object({
  kind: z.enum(["company", "founder", "registry"]), entity_id: z.string(), company_id: z.string().nullable(),
  name: z.string(), company: z.string().nullable(), website: z.string().nullable(), description: z.string().nullable(),
  location: z.string().nullable(), region, shortlist_rank: z.number().int().nullable(), rank: z.number().int().nullable(),
  fit_type: fitType.nullable(), final: num.nullable(), status: z.string().nullable(), cin: z.string().nullable(),
  registered_on: z.string().nullable(), paid_up_capital_inr: num.nullable(), authorized_capital_inr: num.nullable(),
  total_raised_usd: num.nullable(),
});
export const policyRow = z.object({
  version: z.number().int(), status: z.enum(["active", "draft", "archived"]), author: z.string(), note: z.string(),
  policy: z.record(z.string(), z.unknown()), changes: z.array(z.record(z.string(), z.unknown())), created_at: z.string(),
  runs_scored: z.coerce.number().int().default(0),
});
export const universeRow = z.object({ area: z.string(), bucket: z.string(), count: z.number().int() });

export type Shortlist = z.infer<typeof shortlistRow>;
export type LeftOut = z.infer<typeof leftOutRow>;
export type LongList = z.infer<typeof longListRow>;
export type CompanyDetail = z.infer<typeof companyDetailRow>;
export type Run = z.infer<typeof runRow>;
export type Funnel = z.infer<typeof funnelRow>;
export type Activity = z.infer<typeof activityRow>;
export type Universe = z.infer<typeof universeRow>;
export type SearchHit = z.infer<typeof searchRow>;
export type PolicyVersionRow = z.infer<typeof policyRow>;
export type FounderProfile = z.infer<typeof founderProfile>;

/** Validate rows; drop (and log) invalid ones instead of crashing the page. */
export function parseRows<S extends z.ZodTypeAny>(schema: S, rows: unknown[], view: string): z.infer<S>[] {
  const out: z.infer<S>[] = [];
  for (const r of rows) {
    const res = schema.safeParse(r);
    if (res.success) out.push(res.data);
    else console.error(`[${view}] dropped invalid row:`, res.error.issues.slice(0, 3));
  }
  return out;
}

// --- contract drift checks (compile time only) ---------------------------------------------------------
type Assignable<A, B> = [A] extends [B] ? true : false;
const drift: [
  Assignable<Shortlist, ShortlistRow>, Assignable<LeftOut, LeftOutRow>, Assignable<LongList, LongListRow>,
  Assignable<Funnel, FunnelRow>, Assignable<Activity, ActivityRow>, Assignable<Universe, UniverseRow>,
  Assignable<SearchHit["kind"], SearchRow["kind"]>,
  Assignable<Run["status"], RunRow["status"]>, Assignable<CompanyDetail["status"], CompanyDetailRow["status"]>,
] = [true, true, true, true, true, true, true, true, true];
void drift;
