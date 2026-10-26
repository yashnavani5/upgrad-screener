import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { StageStepper } from "@/components/stage-stepper";
import { EmptyState, PageHeader, Skeleton, StatusPill } from "@/components/ui";
import { STAGES, STAGE_LABEL, clean, count, day, duration, time } from "@/lib/format";
import { getRuns } from "@/lib/queries";

export const metadata: Metadata = { title: "Runs" };

async function RunList() {
  const runs = await getRuns(30);
  if (!runs.length) return <EmptyState title="No runs yet">Start one with Run now.</EmptyState>;
  return (
    <ol className="grid gap-4" data-testid="runs-list">
      {runs.map((r) => {
        const cps = (r.checkpoints ?? {}) as Record<string, { status?: string; summary?: string | null }>;
        const opts = r.options as { top?: number; incremental?: boolean };
        return (
          <li key={r.run_id} className="card card-hover animate-slide-up p-5 grid gap-4" data-testid="run-card">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-sm">{r.run_id.slice(0, 8)}</span>
              <StatusPill status={r.status} />
              {r.is_latest && <span className="chip bg-brand-subtle text-brand-hover">Published on the dashboard</span>}
              <span className="text-sm text-ink-muted">
                {day(r.started_at)} {time(r.started_at)}, {duration(r.started_at, r.finished_at)}, triggered by {r.trigger}
                {opts.top ? `, top ${opts.top}` : ""}{opts.incremental === false ? ", full" : opts.incremental ? ", incremental" : ""}
              </span>
              {(r.status === "succeeded" || r.status === "partial") && (r.counts.candidates ?? 0) > 0 && (
                <span className="ml-auto flex gap-3 text-sm">
                  <Link className="link" href={`/shortlist?run=${r.run_id}`}>Shortlist</Link>
                  <Link className="link" href={`/explore?run=${r.run_id}`}>Long list</Link>
                </span>
              )}
            </div>
            {Object.keys(cps).length > 0 && <StageStepper run={r} />}
            <dl className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-sm">
              {[["Candidates", r.counts.candidates], ["Companies", r.counts.companies], ["Founders", r.counts.founders],
                ["Verified claims", r.counts.claims_supported], ["Shortlisted", r.counts.shortlisted]].map(([k, v]) => (
                <div key={k as string}>
                  <dt className="text-ink-muted">{k}</dt>
                  {/* counts other than candidates are written at the score stage */}
                  <dd className="font-semibold tabular">{r.status === "running" && !v ? <span className="font-normal text-ink-muted">After scoring</span> : count(v as number)}</dd>
                </div>
              ))}
            </dl>
            {Object.keys(cps).length > 0 && (
              <details className="text-sm">
                <summary className="cursor-pointer text-ink-muted">Stage details</summary>
                <ul className="mt-2 grid gap-1">
                  {STAGES.filter((s) => cps[s]).map((s) => (
                    <li key={s}><span className="font-medium">{STAGE_LABEL[s]}:</span> {cps[s].status}{cps[s].summary ? `, ${clean(cps[s].summary)}` : ""}</li>
                  ))}
                </ul>
              </details>
            )}
            {r.error_summary && <p className="text-sm text-danger">Errors: {clean(r.error_summary)}</p>}
            {(r.status === "partial" || r.status === "failed") && (
              <p className="text-xs text-ink-muted">Resume from a terminal: <code className="font-mono">uv run screener run --resume {r.run_id}</code></p>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export default function RunsPage() {
  return (
    <>
      <PageHeader title="Runs" subtitle="Every pipeline run with its stages, counts and errors. Runs are resumable from the last completed stage." />
      <Suspense fallback={<div className="grid gap-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-48" />)}</div>}>
        <RunList />
      </Suspense>
    </>
  );
}
