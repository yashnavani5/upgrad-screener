"use client";

import { Download, FileText, LayoutGrid, Table2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { fitLabel, useFitLabels } from "@/components/fit-badge";
import { clean, score } from "@/lib/format";
import type { RegionFilter } from "@/lib/region";
import type { Shortlist } from "@/lib/schemas";
import { SCORE_INFO } from "@/lib/score-labels";

import { ExportButton } from "./export-button";
import { ShortlistCard } from "./shortlist-card";
import { EmptyState, EvidenceLink, FitBadge, RegionBadge } from "./ui";


export function ShortlistView({ rows, run, region = "global" }: { rows: Shortlist[]; run?: string; region?: RegionFilter }) {
  const { labels, ids: fitIds } = useFitLabels(rows.map((r) => r.fit_type));
  const [fits, setFits] = useState<string[]>([]);
  const [view, setView] = useState<"cards" | "table">("cards");
  const shown = useMemo(
    () => rows.filter((r) => !fits.length || fits.includes(r.fit_type)),
    [rows, fits],
  );
  const csv = new URLSearchParams();
  fits.forEach((f) => csv.append("fit", f));
  if (run) csv.set("run", run);
  csv.set("region", region);

  return (
    <div className="grid gap-4">
      <div className="card p-4 flex flex-wrap items-center gap-4" role="group" aria-label="Filters">
        <fieldset className="flex flex-wrap items-center gap-2">
          <legend className="sr-only">Fit type</legend>
          <span className="text-sm text-ink-muted mr-1">Fit type</span>
          {fitIds.map((f) => {
            const on = fits.includes(f);
            return (
              <button key={f} type="button" aria-pressed={on} data-testid={`filter-${f}`}
                      onClick={() => setFits((cur) => (on ? cur.filter((x) => x !== f) : [...cur, f]))}
                      className={`chip h-8 px-3 border ${on ? "border-brand bg-brand-subtle text-brand-hover" : "border-border-strong bg-surface text-ink"}`}>
                {fitLabel(f, labels)}
              </button>
            );
          })}
        </fieldset>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-control border border-border-strong overflow-hidden" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")} data-testid="view-cards"
                    className={`h-9 px-3 inline-flex items-center gap-1 text-sm ${view === "cards" ? "bg-brand-subtle text-brand-hover" : "bg-surface"}`}>
              <LayoutGrid className="w-4 h-4" aria-hidden /> Cards
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")} data-testid="view-table"
                    className={`h-9 px-3 inline-flex items-center gap-1 text-sm border-l border-border-strong ${view === "table" ? "bg-brand-subtle text-brand-hover" : "bg-surface"}`}>
              <Table2 className="w-4 h-4" aria-hidden /> Table
            </button>
          </div>
          <ExportButton href={`/api/shortlist.csv?${csv.toString()}`} expect="text/csv" fallbackName="upgrad-shortlist.csv"
                        className="btn-secondary h-9" testId="export-csv" busyLabel="Exporting...">
            <Download className="w-4 h-4" aria-hidden /><span className="hidden sm:inline">Export</span> CSV
          </ExportButton>
          <ExportButton href={`/api/shortlist.pdf?${csv.toString()}`} expect="application/pdf" fallbackName="upgrad-shortlist.pdf"
                        className="btn-secondary h-9" testId="export-pdf" busyLabel="Building PDF..." warmUrl="/api/shortlist.pdf?warm=1"
                        title="A4 report with every card, the curated left-out picks and source links">
            <FileText className="w-4 h-4" aria-hidden /><span className="hidden sm:inline">Export</span> PDF
          </ExportButton>
        </div>
      </div>

      {shown.length === 0 ? (
        <EmptyState title="No entries match these filters">
          <button type="button" className="link" onClick={() => setFits([])}>Reset filters</button>
        </EmptyState>
      ) : view === "cards" ? (
        <ol className="grid md:grid-cols-2 gap-4" data-testid="shortlist-cards">
          {shown.map((r) => (
            <ShortlistCard key={r.entity_id} r={r} run={run} />
          ))}
        </ol>
      ) : (
        <div className="card overflow-x-auto" data-testid="shortlist-table">
          <table className="w-full min-w-[960px]">
            <thead className="bg-surface-alt"><tr>
              {["#", "Company", "Founder(s)", "Fit", "Region"].map((h) => <th key={h} className="th">{h}</th>)}
              {(["final", "t", "i", "f", "c"] as const).map((k) => <th key={k} className="th" title={SCORE_INFO[k].hint}>{SCORE_INFO[k].name}</th>)}
              {["Why they made the cut", "Evidence"].map((h) => <th key={h} className="th">{h}</th>)}
            </tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.entity_id} className="border-t border-border">
                  <td className="td tabular">{r.rank}</td>
                  <td className="td font-medium"><Link className="hover:text-brand-strong" href={`/company/${r.entity_id}${run ? `?run=${run}` : ""}`}>{r.company}</Link></td>
                  <td className="td">{r.founders.map((f) => f.name).join(", ") || "-"}</td>
                  <td className="td"><FitBadge fit={r.fit_type} /></td>
                  <td className="td"><RegionBadge region={r.region} basis={r.region_basis} /></td>
                  {[r.final, r.t, r.i, r.f, r.confidence].map((v, i) => <td key={i} className="td tabular">{score(v, i === 0 ? 3 : 2)}</td>)}
                  <td className="td text-ink-muted max-w-sm">{clean(r.why_line)}</td>
                  <td className="td"><div className="flex flex-col gap-1">{r.evidence.slice(0, 3).map((e) => <EvidenceLink key={e.url} url={e.url}>{e.domain}</EvidenceLink>)}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
