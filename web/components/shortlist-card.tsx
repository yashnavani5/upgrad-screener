import Link from "next/link";

import { clean, day } from "@/lib/format";
import type { Shortlist } from "@/lib/schemas";
import { SCORE_INFO } from "@/lib/score-labels";

import { FounderList } from "./founders";
import { EvidenceLink, FitBadge, RegionBadge, ScoreBar } from "./ui";

/** One shortlist pick: the same card on the Shortlist page and in the PDF report (`print`: no motion, full links). */
export function ShortlistCard({ r, run, print = false }: { r: Shortlist; run?: string; print?: boolean }) {
  return (
    <li className={`card p-5 flex flex-col gap-4 ${print ? "print-card" : "card-hover animate-slide-up"}`} data-testid="shortlist-card">
      <div className="flex items-start gap-3">
        <span className="w-9 h-9 shrink-0 rounded-full bg-brand-strong text-white font-semibold flex items-center justify-center tabular">{r.rank}</span>
        <div className="min-w-0 flex-1">
          {print ? <h2 className="text-lg font-semibold">{r.company}</h2> : (
            <Link href={`/company/${r.entity_id}${run ? `?run=${run}` : ""}`} className="text-lg font-semibold hover:text-brand-strong" data-testid="company-link">
              {r.company}
            </Link>
          )}
          <div className="flex flex-wrap items-center gap-2 mt-1">
            <FitBadge fit={r.fit_type} />
            <RegionBadge region={r.region} basis={r.region_basis} />
            {!r.size_verified && <span className="chip bg-warning-subtle text-warning" title="No verified funding or headcount: the size check could not confirm this company is under the ceiling." data-testid="size-unverified">Size not verified</span>}
            {r.website && <EvidenceLink url={r.website} />}
            {r.reviewer_label && <span className="chip bg-surface-alt">{r.reviewer_label}</span>}
          </div>
        </div>
      </div>
      {(() => {
        const about = (
          <>
            {r.description && <p className="text-sm text-ink-muted" data-testid="company-description">{clean(r.description)}</p>}
            <div className="rounded-control bg-surface-alt/60 p-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-muted mb-2">Founders</h3>
              <FounderList founders={r.founders} compact />
            </div>
          </>
        );
        const scores = (
          <>
            <div className="grid gap-1.5">
              {([["final", r.final], ["t", r.t], ["i", r.i], ["f", r.f], ["c", r.confidence]] as const).map(([k, v]) => (
                <ScoreBar key={k} label={SCORE_INFO[k].name} hint={SCORE_INFO[k].hint} value={v} emphasis={k === "final"} labelWidth="w-28" />
              ))}
              <p className="text-[11px] leading-snug text-ink-muted">
                Scores run 0 to 1. Track record = how proven the founders are; Interesting = momentum and novelty;
                Fit = match with upGrad&apos;s needs; Confidence = share of facts verified. Hover a bar for details.
              </p>
            </div>
            <dl className="grid gap-2 text-sm">
              <div><dt className="font-medium">Why they made the cut</dt><dd className="text-ink-muted">{clean(r.why_line) || "Pending."}</dd></div>
              <div><dt className="font-medium">How they fit upGrad</dt><dd className="text-ink-muted">{clean(r.fit_line) || "Pending."}</dd></div>
              {r.reviewer_note && (
                <div className="border-l-2 border-border-strong pl-3" data-testid="reviewer-note">
                  <dt className="font-medium">Reviewer note</dt>
                  <dd className="text-ink-muted">{r.reviewer_note}</dd>
                  <dd className="text-[11px] text-ink-muted mt-0.5">
                    {r.reviewer ? `From ${r.reviewer}. ` : ""}The reviewer&apos;s view, not a source-verified fact.
                  </dd>
                </div>
              )}
            </dl>
          </>
        );
        // The PDF puts the company and founders beside the scores and reasons, so about two picks fit on an A4 page.
        return print ? (
          <div className="grid grid-cols-[1.25fr_1fr] gap-5 items-start">
            <div className="grid gap-3 min-w-0">{about}</div>
            <div className="grid gap-3 min-w-0">{scores}</div>
          </div>
        ) : <>{about}{scores}</>;
      })()}
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-auto pt-2 border-t border-border">
        {r.evidence.slice(0, 3).map((e) => <EvidenceLink key={e.url} url={e.url}>{e.domain}</EvidenceLink>)}
        <span className="ml-auto text-xs text-ink-muted self-center">Last verified {day(r.last_verified)}</span>
      </div>
    </li>
  );
}
