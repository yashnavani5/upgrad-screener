export type ReportParams = { run?: string; region?: string; fit?: string | string[]; min_confidence?: string; print?: string };

/** Query string shared by the report page, the PDF route and the CSV export (same filters as the Shortlist page). */
export function reportQuery(p: ReportParams): URLSearchParams {
  const q = new URLSearchParams();
  for (const f of [p.fit ?? []].flat()) q.append("fit", f);
  if (p.min_confidence) q.set("min_confidence", p.min_confidence);
  if (p.run) q.set("run", p.run);
  if (p.region) q.set("region", p.region);
  return q;
}
