import { activeFitLabels, fitName } from "@/lib/fit-labels";
import { founderText } from "@/components/founders";
import { clean } from "@/lib/format";
import { getShortlist } from "@/lib/queries";
import { REGION_LABEL, inRegion, parseRegion } from "@/lib/region";

const COLUMNS = ["Rank", "Founder(s)", "Company", "Website", "Fit type", "Final", "T", "I", "F", "Confidence",
  "Why they made the cut", "How they fit upGrad", "Founders: why they made the cut",
  "Founders: how they fit upGrad", "Evidence 1", "Evidence 2", "Evidence 3", "LinkedIn URL",
  "Last verified", "entity_id", "Region"];

function cell(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// CSV export in the Google Sheet's Shortlist column order (filters from the page are applied).
export async function GET(request: Request) {
  const url = new URL(request.url);
  const fits = url.searchParams.getAll("fit");
  const minConf = Number(url.searchParams.get("min_confidence") ?? 0);
  const region = parseRegion(url.searchParams.get("region") ?? "global");  // API default: every row
  const rows = (await getShortlist(url.searchParams.get("run")))
    .filter((r) => (!fits.length || fits.includes(r.fit_type)) && r.confidence >= minConf && inRegion(r, region));
  const { labels } = await activeFitLabels();
  const lines = [COLUMNS.join(",")];
  for (const r of rows) {
    const ev = [...r.evidence.map((e) => e.url), "", "", ""];
    lines.push([
      r.rank, r.founders.map((f) => f.name).join("; "), r.company, r.website, fitName(r.fit_type, labels), r.final.toFixed(3),
      r.t.toFixed(3), r.i.toFixed(3), r.f.toFixed(3), r.confidence.toFixed(3), clean(r.why_line), clean(r.fit_line),
      ...(["why", "fit"] as const).map((k) => r.founders.filter((f) => f[k]).map((f) => `${f.name}: ${founderText(f.name, f[k])}`).join("\n")),
      ev[0], ev[1], ev[2], r.founders.map((f) => f.linkedin_url).filter(Boolean).join("; "), r.last_verified ?? "", r.entity_id,
      REGION_LABEL[r.region] ?? r.region,
    ].map(cell).join(","));
  }
  return new Response("﻿" + lines.join("\r\n") + "\r\n", {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="upgrad-shortlist-${new Date().toISOString().slice(0, 10)}.csv"`,
      "Cache-Control": "no-store",
    },
  });
}
