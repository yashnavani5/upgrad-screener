import { FileSpreadsheet } from "lucide-react";
import Link from "next/link";
import { Suspense } from "react";

import { FitSplit, FunnelChart } from "@/components/charts";
import { LiveOverview } from "@/components/live-overview";
import { AskButton } from "@/components/ask-box";
import { EmptyState, FitBadge, PageHeader, Skeleton } from "@/components/ui";
import { clean, score } from "@/lib/format";
import { getFunnel, getShortlist } from "@/lib/queries";

async function Funnel() {
  const rows = await getFunnel();
  if (!rows.length) return <EmptyState title="No funnel yet">The funnel appears after the first scored run.</EmptyState>;
  return <FunnelChart rows={rows.map((r) => ({ stage: r.stage, count: r.count }))} />;
}

async function ShortlistPreview() {
  const rows = await getShortlist();
  if (!rows.length) {
    return <EmptyState title="No shortlist yet">Start a run with Run now. The shortlist appears when scoring finishes.</EmptyState>;
  }
  const split = ["acquire", "partner", "co_build"].map((fit) => ({ fit, count: rows.filter((r) => r.fit_type === fit).length }));
  return (
    <div className="grid lg:grid-cols-3 gap-6">
      <section className="card p-5" aria-label="Fit split">
        <h2 className="text-lg font-semibold mb-2">Shortlist by fit type</h2>
        <FitSplit rows={split} />
      </section>
      <section className="card p-5 lg:col-span-2" aria-label="Top of the shortlist">
        <div className="flex items-center mb-3">
          <h2 className="text-lg font-semibold">Top of the shortlist</h2>
          <Link href="/shortlist" className="ml-auto link text-sm font-medium">See all {rows.length}</Link>
        </div>
        <ol className="divide-y divide-border">
          {rows.slice(0, 5).map((r) => (
            <li key={r.entity_id} className="py-3 flex items-start gap-3">
              <span className="w-7 h-7 shrink-0 rounded-full bg-brand-subtle text-brand-hover text-sm font-semibold flex items-center justify-center tabular">{r.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Link href={`/company/${r.entity_id}`} className="font-semibold hover:text-brand-strong">{r.company}</Link>
                  <FitBadge fit={r.fit_type} />
                  <span className="text-xs text-ink-muted tabular">Final {score(r.final, 3)}</span>
                </div>
                <p className="text-sm text-ink-muted mt-1 line-clamp-2">{clean(r.why_line) || "Explanation pending."}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}

export default function OverviewPage() {
  const sheet = process.env.GOOGLE_SHEET_URL;
  return (
    <>
      <PageHeader title="Overview" subtitle="Education founders and companies, every fact verified against its source.">
        <AskButton />
        {sheet && (
          <a href={sheet} target="_blank" rel="noopener noreferrer" className="btn-secondary" data-testid="open-sheet">
            <FileSpreadsheet className="w-4 h-4" aria-hidden /> Open Google Sheet
          </a>
        )}
      </PageHeader>
      <div className="grid gap-6">
        <LiveOverview />
        <section className="card p-5" aria-label="Funnel">
          <h2 className="text-lg font-semibold mb-2">Funnel, latest published run</h2>
          <Suspense fallback={<Skeleton className="h-72" />}>
            <Funnel />
          </Suspense>
        </section>
        <Suspense fallback={<Skeleton className="h-72" />}>
          <ShortlistPreview />
        </Suspense>
      </div>
    </>
  );
}
