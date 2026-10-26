import Link from "next/link";

import { clean } from "@/lib/format";
import type { LeftOut } from "@/lib/schemas";

import { EvidenceLink, ReasonBadge, RegionBadge } from "./ui";

const CATEGORY: Record<string, { title: string; hint: string }> = {
  big: { title: "Already a scaled player", hint: "Strong, but too large, a scaled competitor, or already acquired" },
  evidence: { title: "Not enough verified evidence", hint: "Interesting, but the facts we need could not be verified" },
  close: { title: "A close call", hint: "Good fit, beaten by a stronger or very similar pick" },
};

/** The run's curated "left out" picks (explain stage): one per category, each with a cited reason. */
export function CuratedLeftOut({ rows }: { rows: LeftOut[] }) {
  const picks = rows.filter((r) => r.featured).sort((a, b) => (a.featured ?? 9) - (b.featured ?? 9));
  if (!picks.length) return null;
  return (
    <section aria-label="Curated: who we left out and why" className="grid gap-3" data-testid="curated-left-out">
      <div>
        <h2 className="text-lg font-semibold">Who we left out, and why</h2>
        <p className="text-sm text-ink-muted">Three companies that came close, one per reason, chosen by rule and explained from verified claims.</p>
      </div>
      <ol className="grid md:grid-cols-3 gap-4">
        {picks.map((r) => {
          const cat = CATEGORY[r.featured_category ?? ""] ?? { title: "Left out", hint: "" };
          return (
            <li key={r.entity_id} className="card card-hover animate-slide-up p-5 flex flex-col gap-3" data-testid="curated-card">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-brand-strong">{cat.title}</p>
                <p className="text-xs text-ink-muted">{cat.hint}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/company/${r.entity_id}`} className="text-lg font-semibold hover:text-brand-strong" data-testid="curated-company">{r.company}</Link>
                <ReasonBadge code={r.reason_code} />
                <RegionBadge region={r.region} />
              </div>
              {r.would_have_ranked ? <p className="text-xs text-ink-muted">Would have ranked #{r.would_have_ranked} by score</p> : null}
              <p className="text-sm" data-testid="curated-line">{clean(r.featured_line || r.explanation)}</p>
              {r.description && <p className="text-sm text-ink-muted">{clean(r.description)}</p>}
              <div className="mt-auto pt-2 border-t border-border text-sm flex flex-wrap gap-3">
                {r.founders.length > 0 && <span className="text-ink-muted">Founders: {r.founders.map((f) => f.name).join(", ")}</span>}
                {r.source_url && <EvidenceLink url={r.source_url}>Source</EvidenceLink>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
