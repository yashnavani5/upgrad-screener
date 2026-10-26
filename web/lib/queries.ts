import "server-only";

import { connection } from "next/server";

import { plain, sql } from "./db";
import {
  activityRow, companyDetailRow, funnelRow, leftOutRow, longListRow, parseRows, policyRow, runRow, searchRow, shortlistRow, universeRow,
  type Activity, type CompanyDetail, type Funnel, type LeftOut, type LongList, type PolicyVersionRow, type Run, type SearchHit, type Shortlist,
  type Universe,
} from "./schemas";

// Every read happens at request time (live data). `run` selects a specific run; default is the latest published.
const UUID = /^[0-9a-f-]{36}$/i;

function runFilter(run?: string | null) {
  const db = sql();
  return run && UUID.test(run) ? db`run_id = ${run}` : db`is_latest`;
}

export async function getShortlist(run?: string | null): Promise<Shortlist[]> {
  await connection();
  const rows = await sql()`select * from v_shortlist where ${runFilter(run)} order by rank`;
  return parseRows(shortlistRow, plain(rows), "v_shortlist");
}

export async function getLeftOut(run?: string | null): Promise<LeftOut[]> {
  await connection();
  const rows = await sql()`select * from v_left_out where ${runFilter(run)} order by would_have_ranked nulls last, company`;
  return parseRows(leftOutRow, plain(rows), "v_left_out");
}

export async function getLongList(run?: string | null): Promise<LongList[]> {
  await connection();
  const rows = await sql()`select * from v_long_list where ${runFilter(run)} order by rank`;
  return parseRows(longListRow, plain(rows), "v_long_list");
}

export async function getCompany(entityId: string, run?: string | null): Promise<CompanyDetail | null> {
  await connection();
  if (!UUID.test(entityId)) return null;
  const rows = await sql()`select * from v_company_detail where entity_id = ${entityId} and ${runFilter(run)} limit 1`;
  const parsed = parseRows(companyDetailRow, plain(rows), "v_company_detail");
  if (parsed.length) return parsed[0];
  // Not in the selected run: show the most recent run that has it.
  const any = await sql()`select * from v_company_detail where entity_id = ${entityId} order by is_latest desc limit 1`;
  return parseRows(companyDetailRow, plain(any), "v_company_detail")[0] ?? null;
}

export async function getRuns(limit = 50): Promise<Run[]> {
  await connection();
  const rows = await sql()`select * from v_runs order by started_at desc limit ${limit}`;
  return parseRows(runRow, plain(rows), "v_runs");
}

export async function getRun(run?: string | null): Promise<Run | null> {
  await connection();
  const rows = run && UUID.test(run)
    ? await sql()`select * from v_runs where run_id = ${run}`
    : await sql()`select * from v_runs where is_latest limit 1`;
  return parseRows(runRow, plain(rows), "v_runs")[0] ?? null;
}

// A run whose process died (killed, machine slept) never writes a final status; without a heartbeat for this long it
// no longer counts as active, so it cannot block "Run now" or a re-score forever. Stages heartbeat far more often.
const STALE_RUN_MINUTES = 60;

export async function getActiveRun(): Promise<Run | null> {
  await connection();
  const rows = await sql()`select * from v_runs where status in ('queued', 'running')
                           and coalesce(heartbeat_at, started_at) > now() - make_interval(mins => ${STALE_RUN_MINUTES})
                           order by started_at desc limit 1`;
  return parseRows(runRow, plain(rows), "v_runs")[0] ?? null;
}

export async function getFunnel(run?: string | null): Promise<Funnel[]> {
  await connection();
  const rows = await sql()`select * from v_funnel where ${runFilter(run)} order by stage_order`;
  return parseRows(funnelRow, plain(rows), "v_funnel");
}

export async function getActivity(): Promise<Activity[]> {
  await connection();
  const rows = await sql()`select * from v_activity order by calls_5m desc, calls_1h desc`;
  return parseRows(activityRow, plain(rows), "v_activity");
}

export async function getUniverse(): Promise<Universe[]> {
  await connection();
  const rows = await sql()`select * from v_universe order by area, bucket`;
  return parseRows(universeRow, plain(rows), "v_universe");
}

/** Everything the live panels need, in one round trip from the browser. */
export async function getLive() {
  const [latest, active, activity, universe] = await Promise.all([getRun(), getActiveRun(), getActivity(), getUniverse()]);
  return { latest, active, activity, universe, at: new Date().toISOString() };
}
export type Live = Awaited<ReturnType<typeof getLive>>;

/** Name search over companies, founders and registry entries (v_search). Exact and prefix matches first, then
 *  companies on the shortlist, then founders, then registry rows. */
export async function searchEntities(q: string, limit = 12): Promise<SearchHit[]> {
  await connection();
  const term = q.trim().slice(0, 80);
  if (term.length < 2) return [];
  const like = `%${term.replace(/[%_\\]/g, (m) => `\\${m}`)}%`; // LIKE wildcards in the input are literal
  const rows = await sql()`
    select * from v_search
    where name ilike ${like} or company ilike ${like}
    order by (lower(name) = lower(${term})) desc, (name ilike ${term + "%"}) desc,
             (kind = 'company') desc, (kind = 'founder') desc, shortlist_rank nulls last, rank nulls last, name
    limit ${limit}`;
  return parseRows(searchRow, plain(rows), "v_search");
}

/** Shortlisted companies of the latest published run with their verified total raised (v_search). */
export async function shortlistFunding(): Promise<SearchHit[]> {
  await connection();
  const rows = await sql()`select * from v_search where kind = 'company' and shortlist_rank is not null
                           order by total_raised_usd desc nulls last, shortlist_rank`;
  return parseRows(searchRow, plain(rows), "v_search");
}

/** Every saved AI Policy version, newest first (v_policy). */
export async function getPolicyVersions(): Promise<PolicyVersionRow[]> {
  await connection();
  const rows = await sql()`select * from v_policy order by version desc`;
  return parseRows(policyRow, plain(rows), "v_policy");
}
