"use client";

import { Activity, Building2, CheckCircle2, Database, Search, Star } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { count, duration, time } from "@/lib/format";
import { useLive } from "@/lib/use-live";

import { StageStepper } from "./stage-stepper";
import { ErrorState, Skeleton, StatusPill } from "./ui";

function uni(rows: { area: string; bucket: string; count: number }[] | undefined, area: string, bucket?: string) {
  return (rows ?? []).filter((r) => r.area === area && (!bucket || r.bucket === bucket)).reduce((s, r) => s + r.count, 0);
}

function Kpi({ label, value, hint, icon, href }: { label: string; value: string; hint?: string; icon: ReactNode; href: string }) {
  return (
    <Link href={href} className="card card-hover p-4 sm:p-5 flex flex-col gap-2 min-w-0 group" data-testid="kpi">
      <div className="flex items-start gap-2 text-sm text-ink-muted leading-snug"><span className="mt-0.5 shrink-0">{icon}</span>{label}</div>
      <div className="text-2xl font-semibold tabular" data-testid="kpi-value">{value}</div>
      {hint && <div className="text-xs text-ink-muted">{hint}</div>}
      <span className="text-xs text-brand-strong opacity-0 group-hover:opacity-100 transition-opacity">Open</span>
    </Link>
  );
}

export function LiveOverview() {
  const { data, error, isLoading } = useLive();
  if (error) return <ErrorState title="Live data is unavailable" detail="The dashboard cannot reach the database. Check that Postgres is running." />;
  if (isLoading || !data) {
    return (
      <div className="grid gap-4">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28" />)}</div>
        <Skeleton className="h-24" />
      </div>
    );
  }
  const run = data.active ?? data.latest;
  const counts = data.latest?.counts;
  return (
    <div className="grid gap-6">
      <section aria-label="Key metrics" className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi label="Candidates found" value={count(counts?.candidates)} hint="Latest published run" icon={<Search className="w-4 h-4" aria-hidden />} href="/explore?region=global" />
        <Kpi label="Companies resolved" value={count(uni(data.universe, "companies", "total"))}
             hint={`${count(uni(data.universe, "companies", "verified"))} verified`} icon={<Building2 className="w-4 h-4" aria-hidden />} href="/explore?region=global" />
        <Kpi label="Claims verified" value={count(uni(data.universe, "claims", "supported"))}
             hint={`${count(uni(data.universe, "claims", "unverified"))} waiting for verification`} icon={<CheckCircle2 className="w-4 h-4" aria-hidden />} href="/methodology" />
        <Kpi label="Shortlisted" value={count(counts?.shortlisted)} hint="Latest published run" icon={<Star className="w-4 h-4" aria-hidden />} href="/shortlist" />
      </section>

      <section className="card p-5 min-w-0" aria-label="Pipeline run" aria-live="polite">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <h2 className="text-lg font-semibold">{data.active ? "Run in progress" : "Latest run"}</h2>
          {run && <StatusPill status={run.status} />}
          {run && (
            <span className="text-sm text-ink-muted">
              Started {time(run.started_at)}, {duration(run.started_at, run.finished_at)}
              {data.active && run.heartbeat_at ? `, last heartbeat ${time(run.heartbeat_at)}` : ""}
            </span>
          )}
          <span className="ml-auto text-xs text-ink-muted">Updated {time(data.at)}{data.active ? ", every 5 s" : ", every 60 s"}</span>
        </div>
        {run ? <StageStepper run={run} /> : <p className="text-sm text-ink-muted">No runs yet. Start one with Run now.</p>}
      </section>

      <div className="grid lg:grid-cols-2 gap-6">
        <section className="card p-5 min-w-0" aria-label="Live activity">
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2"><Activity className="w-5 h-5 text-brand" aria-hidden />Live activity</h2>
          {data.activity.length === 0 ? (
            <p className="text-sm text-ink-muted">No external calls in the last hour.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full" data-testid="activity-table">
                <thead><tr><th className="th">Source</th><th className="th text-right">Last 5 min</th><th className="th text-right">Errors</th><th className="th text-right">Last hour</th><th className="th text-right">Median ms</th></tr></thead>
                <tbody>
                  {data.activity.map((a) => (
                    <tr key={a.provider} className="border-t border-border">
                      <td className="td font-medium">{a.provider}</td>
                      <td className="td text-right tabular">{count(a.calls_5m)}</td>
                      <td className="td text-right tabular">{a.errors_5m ? <span className="text-danger">{count(a.errors_5m)}</span> : "0"}</td>
                      <td className="td text-right tabular">{count(a.calls_1h)}</td>
                      <td className="td text-right tabular">{count(a.median_latency_ms_1h)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="card p-5 min-w-0" aria-label="Data universe">
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2"><Database className="w-5 h-5 text-brand" aria-hidden />Data universe and queue</h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            {[
              ["Registry rows", uni(data.universe, "registry")],
              ["Prospects", uni(data.universe, "prospects")],
              ["Founders", uni(data.universe, "founders", "total")],
              ["Identity verified", uni(data.universe, "founders", "identity verified")],
              ["Claims contradicted", uni(data.universe, "claims", "contradicted")],
              ["Claims not found", uni(data.universe, "claims", "not_found")],
              ["Tasks queued", (data.universe ?? []).filter((r) => r.area === "tasks" && r.bucket.endsWith("queued")).reduce((s, r) => s + r.count, 0)],
              ["Tasks done", (data.universe ?? []).filter((r) => r.area === "tasks" && r.bucket.endsWith("done")).reduce((s, r) => s + r.count, 0)],
            ].map(([k, v]) => (
              <div key={k as string} className="flex items-baseline justify-between border-b border-border pb-2">
                <dt className="text-ink-muted">{k}</dt>
                <dd className="font-semibold tabular">{count(v as number)}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </div>
  );
}
