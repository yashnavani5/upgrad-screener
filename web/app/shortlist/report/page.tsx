import { Download } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { CuratedLeftOut } from "@/components/curated-left-out";
import { ExportButton } from "@/components/export-button";
import { PrintTrigger } from "@/components/print-trigger";
import { ShortlistCard } from "@/components/shortlist-card";
import { EmptyState, Skeleton } from "@/components/ui";
import { activeFitLabels, fitName } from "@/lib/fit-labels";
import { day } from "@/lib/format";
import { getLeftOut, getRun, getShortlist } from "@/lib/queries";
import { REGION_LABEL, inRegion, parseRegion } from "@/lib/region";
import { type ReportParams as Params, reportQuery } from "@/lib/report";

export const metadata: Metadata = { title: "Shortlist report" };

async function Report({ searchParams }: { searchParams: Promise<Params> }) {
  const p = await searchParams;
  const region = parseRegion(p.region ?? "global");
  const fits = [p.fit ?? []].flat();
  const minConf = Number(p.min_confidence ?? 0) || 0;
  const [all, run, leftOut, { labels }] = await Promise.all([getShortlist(p.run), getRun(p.run), getLeftOut(p.run), activeFitLabels()]);
  const rows = all.filter((r) => (!fits.length || fits.includes(r.fit_type)) && r.confidence >= minConf && inRegion(r, region));
  if (!all.length) return <EmptyState title="No shortlist for this run">The shortlist is written when a run finishes scoring and explanation.</EmptyState>;

  const byFit = Object.entries(rows.reduce<Record<string, number>>((m, r) => ({ ...m, [r.fit_type]: (m[r.fit_type] ?? 0) + 1 }), {}));
  const india = rows.filter((r) => r.region === "india").length;
  const filters = [
    `${REGION_LABEL[region] ?? region} view`,
    fits.length ? `fit: ${fits.map((f) => fitName(f, labels)).join(", ")}` : null,
    minConf ? `confidence at least ${minConf.toFixed(2)}` : null,
  ].filter(Boolean).join("; ");
  const pdf = `/api/shortlist.pdf?${reportQuery(p).toString()}`;

  return (
    <article className="grid gap-6" data-testid="report">
      <PrintTrigger enabled={p.print === "1"} />
      <header className="card p-6 grid gap-4 print-card">
        <div className="flex items-start gap-3">
          <span className="block w-1 self-stretch rounded-full bg-brand" aria-hidden />
          <div className="flex-1">
            <p className="text-sm font-medium text-ink-muted">Founder Screener for upGrad</p>
            <h1 className="text-2xl font-semibold">Founder shortlist</h1>
            <p className="text-sm text-ink-muted">
              {rows.length} pick{rows.length === 1 ? "" : "s"} ({filters}). Run {run?.run_id.slice(0, 8) ?? "latest"}
              {run?.started_at ? ` of ${day(run.started_at)}` : ""}.
            </p>
          </div>
          <div className="flex gap-2 print:hidden">
            <Link href={`/shortlist?${reportQuery({ ...p, fit: undefined, min_confidence: undefined }).toString()}`} className="btn-ghost h-9">Back</Link>
            <ExportButton href={pdf} expect="application/pdf" fallbackName="upgrad-shortlist.pdf" className="btn-primary h-9"
                          testId="report-download" busyLabel="Building PDF...">
              <Download className="w-4 h-4" aria-hidden />Download PDF
            </ExportButton>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          <span className="chip bg-surface-alt">India-located: {india} of {rows.length}</span>
          {byFit.map(([f, n]) => <span key={f} className="chip bg-surface-alt">{fitName(f, labels)}: {n}</span>)}
        </div>
        <p className="text-sm text-ink-muted">
          How to read a card: <strong className="text-ink">T</strong> founder track record, <strong className="text-ink">I</strong> how
          interesting the company is (momentum and novelty), <strong className="text-ink">F</strong> fit with upGrad,
          <strong className="text-ink"> C</strong> share of facts verified. Final combines them and the pairwise AI judge. Every fact
          behind these lines was verified against the linked source; the scores are computed in code, never by the AI.
        </p>
      </header>

      {rows.length === 0
        ? <EmptyState title="No entries match these filters">Change the filters on the Shortlist page.</EmptyState>
        : <ol className="grid gap-4" data-testid="report-cards">{rows.map((r) => <ShortlistCard key={r.entity_id} r={r} run={p.run} print />)}</ol>}

      <div className="print-break"><CuratedLeftOut rows={leftOut} /></div>
    </article>
  );
}

export default function ReportPage({ searchParams }: { searchParams: Promise<Params> }) {
  return (
    <Suspense fallback={<div className="grid gap-4"><Skeleton className="h-40" /><Skeleton className="h-96" /></div>}>
      <Report searchParams={searchParams} />
    </Suspense>
  );
}
