import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { ClaimsTable } from "@/components/claims-table";
import { FounderList } from "@/components/founders";
import { EmptyState, EvidenceLink, FitBadge, PageHeader, RegionBadge, ScoreBar, Skeleton, StatusPill } from "@/components/ui";
import { clean, count, day, score, usd } from "@/lib/format";
import { activeFitLabels, fitName } from "@/lib/fit-labels";
import { getCompany } from "@/lib/queries";
import type { CompanyDetail } from "@/lib/schemas";

export const metadata: Metadata = { title: "Company" };

const LABEL: Record<string, string> = {
  T: "Track record", M: "Momentum", N: "Novelty", I: "Interesting", F: "Fit", C: "Confidence", final: "Final",
  bt_pct: "Pairwise judge (percentile)", final_prime: "Final after judge",
};

/** Sub-score name; per-category fit rows (F_<id>) use the active AI Policy's category names. */
function label(name: string, fits: Record<string, string>): string {
  return name.startsWith("F_") ? `Fit: ${fitName(name.slice(2), fits)}` : LABEL[name] ?? name;
}

/** Computed note for the weakest of T, I, F (numbers only; no model-written text). */
function whyNotHigher(c: CompanyDetail, fits: Record<string, string>): string | null {
  const main = c.score_breakdown.filter((s) => ["T", "I", "F"].includes(s.name) && s.adjusted !== null && s.adjusted !== undefined);
  if (!main.length) return null;
  const weakest = main.reduce((a, b) => ((a.adjusted ?? 0) <= (b.adjusted ?? 0) ? a : b));
  const mean = weakest.pool_mean !== null && weakest.pool_mean !== undefined ? `, pool mean ${score(weakest.pool_mean)}` : "";
  return `${label(weakest.name, fits)} is the weakest sub-score: ${score(weakest.adjusted)} from ${weakest.n_signals} verified signal${weakest.n_signals === 1 ? "" : "s"}${mean}. More verified evidence in this area would raise it.`;
}

async function Detail({ params, searchParams }: { params: Promise<{ entityId: string }>; searchParams: Promise<{ run?: string }> }) {
  const [{ entityId }, { run }] = await Promise.all([params, searchParams]);
  const c = await getCompany(entityId, run);
  if (!c) notFound();
  const { labels: fits } = await activeFitLabels();
  const note = whyNotHigher(c, fits);
  const order = ["final_prime", "final", "T", "I", "M", "N", "F", "F_acquire", "F_partner", "F_co_build", "C", "bt_pct"];
  const breakdown = [...c.score_breakdown].sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
  return (
    <>
      <PageHeader title={c.company} subtitle={c.description ? clean(c.description) : undefined}>
        {c.website && <a href={c.website} target="_blank" rel="noopener noreferrer" className="btn-secondary">Visit website</a>}
        <Link href={c.rank ? "/shortlist" : "/left-out"} className="btn-ghost">{c.rank ? "Back to shortlist" : "See left out"}</Link>
      </PageHeader>

      <div className="grid lg:grid-cols-3 gap-6">
        <section className="card p-5 grid gap-3 content-start" aria-label="Profile">
          <h2 className="text-lg font-semibold">Profile</h2>
          <div className="flex flex-wrap gap-2">
            <FitBadge fit={c.fit_type} />
            <RegionBadge region={c.region} basis={c.region_basis} />
            <StatusPill status={c.status} label={c.status === "unknown" ? "Liveness unknown" : c.status === "active" ? "Active" : "Inactive"} />
            {c.rank ? <span className="chip bg-brand-subtle text-brand-hover">Shortlist #{c.rank}</span> : <span className="chip bg-surface-alt text-ink-muted">Not shortlisted</span>}
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {[
              ["Location", [c.city, c.country].filter(Boolean).join(", ") || "-"],
              ["Location evidence", clean(c.region_basis)],
              ["Founded", c.founded_year ?? "-"],
              ["Total raised", usd(c.total_raised_usd)],
              ["Headcount", count(c.headcount)],
              ["Verified claims", count(c.claims.length)],
              ["Run", c.is_latest ? "Latest" : c.run_id.slice(0, 8)],
            ].map(([k, v]) => (
              <div key={k as string}><dt className="text-ink-muted">{k}</dt><dd className="font-medium">{v}</dd></div>
            ))}
          </dl>
        </section>

        <section className="card p-5 lg:col-span-2" aria-label="Score breakdown">
          <h2 className="text-lg font-semibold mb-3">Score breakdown</h2>
          {breakdown.length === 0 ? <p className="text-sm text-ink-muted">Not scored in this run.</p> : (
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2">
              {breakdown.map((s) => (
                <div key={s.name} className="grid gap-0.5">
                  <ScoreBar label={s.name === "final_prime" ? "Final" : s.name} value={s.adjusted ?? s.raw} emphasis={s.name === "final_prime"} />
                  <span className="text-xs text-ink-muted pl-12">{label(s.name, fits)}, {s.n_signals} signal{s.n_signals === 1 ? "" : "s"}{s.raw !== s.adjusted && s.raw !== null && s.raw !== undefined ? `, raw ${score(s.raw)}` : ""}</span>
                </div>
              ))}
            </div>
          )}
          {note && <p className="mt-4 text-sm bg-warning-subtle text-warning rounded-control px-3 py-2" data-testid="why-not-higher">Why not higher: {note}</p>}
        </section>

        <section className="card p-5" aria-label="Founders">
          <h2 className="text-lg font-semibold mb-3">Founders</h2>
          <FounderList founders={c.founders.map((f) => ({ ...f, role: f.is_current ? f.role : `${f.role ?? "founder"} (past)` }))} />
        </section>

        <section className="card p-5 lg:col-span-2" aria-label="Funding timeline">
          <h2 className="text-lg font-semibold mb-3">Funding timeline</h2>
          {c.rounds.length === 0 ? <p className="text-sm text-ink-muted">No corroborated funding rounds.</p> : (
            <ol className="relative border-l-2 border-border ml-2 grid gap-4">
              {c.rounds.map((r, i) => (
                <li key={`${r.source_url}-${i}`} className="pl-4">
                  <span className="absolute -left-[7px] w-3 h-3 rounded-full bg-brand mt-1.5" aria-hidden />
                  <div className="font-medium">{r.round_type ? clean(r.round_type) : "Round"}, {usd(r.amount_usd)}</div>
                  <div className="text-sm text-ink-muted">{day(r.announced_on)}{r.investors.length ? `, ${r.investors.join(", ")}` : ""}</div>
                  <EvidenceLink url={r.source_url} />
                </li>
              ))}
            </ol>
          )}
        </section>

        <section className="card p-5 lg:col-span-3" aria-label="Verified claims">
          <h2 className="text-lg font-semibold mb-1">Verified claims</h2>
          <p className="text-sm text-ink-muted mb-3">Every row links to its source. Hover or focus the quote icon to see the exact evidence.</p>
          {c.claims.length === 0 ? <EmptyState title="No supported claims yet" /> : <ClaimsTable claims={c.claims} />}
        </section>
      </div>
    </>
  );
}

export default function CompanyPage(props: { params: Promise<{ entityId: string }>; searchParams: Promise<{ run?: string }> }) {
  return (
    <Suspense fallback={<div className="grid gap-4"><Skeleton className="h-16" /><Skeleton className="h-64" /><Skeleton className="h-96" /></div>}>
      <Detail {...props} />
    </Suspense>
  );
}
