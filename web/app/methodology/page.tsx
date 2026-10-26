import type { Metadata } from "next";
import { Suspense } from "react";

import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { day } from "@/lib/format";
import { getRun, getRuns } from "@/lib/queries";

export const metadata: Metadata = { title: "Methodology" };

type Dict = Record<string, unknown>;
const get = (o: unknown, ...path: string[]): unknown => path.reduce<unknown>((v, k) => (v && typeof v === "object" ? (v as Dict)[k] : undefined), o);
const n = (v: unknown) => (typeof v === "number" ? v : "-");

function Formula({ children }: { children: React.ReactNode }) {
  return <pre className="bg-surface-alt rounded-control p-3 text-sm whitespace-pre-wrap font-mono">{children}</pre>;
}

async function Content() {
  const published = await getRun();
  // Before the first publish, show the configuration the newest run started with (still real, just not published yet).
  const run = published?.config_snapshot ? published : (await getRuns(10)).find((r) => r.config_snapshot) ?? null;
  if (!run?.config_snapshot) return <EmptyState title="No runs yet">The methodology shows the exact weights used by the latest run.</EmptyState>;
  const w = run.config_snapshot.weights;
  const tw = (get(w, "track_record", "weights") ?? {}) as Dict;
  const ex = (get(w, "final", "exponents") ?? {}) as Dict;
  const gap = run.config_snapshot.gap_map as { business_units?: { id: string; name: string }[]; wanted_capabilities?: { id: string; name: string }[] };
  const excl = run.config_snapshot.exclusions as { competitors_at_scale?: string[]; size_ceiling?: Dict };
  // Categories of the AI Policy this run used (runs before the AI Policy: the three default lenses).
  const cats = ((get(w, "fit", "categories") ?? []) as { label?: string }[]).map((c) => c.label).filter(Boolean);
  const lenses = (cats.length ? cats : ["Acquire", "Partner", "Co-build"]).join(", ");
  return (
    <div className="grid gap-6">
      <p className="text-sm text-ink-muted">Weights and lists below are taken from the configuration snapshot of the {run === published ? "latest published" : "most recent (not yet published)"} run ({run.run_id.slice(0, 8)}, {day(run.started_at)}), so every ranking can be traced to the exact numbers that produced it.</p>

      <section className="card p-5 grid gap-3">
        <h2 className="text-lg font-semibold">How facts are collected and verified</h2>
        <ol className="list-decimal pl-5 text-sm grid gap-2">
          <li>Every fact is a claim with a source URL, a retrieval time, a verbatim evidence quote and a confidence. No source means the fact does not exist.</li>
          <li>Grounding check in code: the quote must appear in the fetched page (fuzzy match of at least 90, and every number must match exactly).</li>
          <li>An independent verifier model sees only the claim and a fresh copy of the page, and answers supported, contradicted or not found.</li>
          <li>Funding amounts, revenue and exits need two independent domains or one primary source (company site, acquirer, government filing).</li>
          <li>The model extracts, verifies and explains. All scores are computed in code.</li>
        </ol>
      </section>

      <section className="card p-5 grid gap-3">
        <h2 className="text-lg font-semibold">Scoring</h2>
        <h3 className="font-medium">Track record (T), best co-founder</h3>
        <Formula>{`T = ${n(tw.prior_exit)} x prior exit + ${n(tw.prior_vc_backed)} x prior VC-backed company + ${n(tw.top_investor)} x top investor
  + ${n(tw.scale_pct_log)} x pct(log scale) + ${n(tw.years_in_edu_pct)} x pct(years in education)
  + ${n(tw.operator_pedigree)} x operator pedigree + ${n(tw.accelerator_or_award)} x accelerator or award
  + ${n(get(w, "track_record", "team_depth_bonus"))} team-depth bonus, capped at ${n(get(w, "track_record", "cap"))}`}</Formula>
        <h3 className="font-medium">Interesting (I)</h3>
        <Formula>{`I = ${n(get(w, "interesting", "weights", "momentum"))} x Momentum + ${n(get(w, "interesting", "weights", "novelty"))} x Novelty
Momentum = mean of momentum percentiles x 0.5^(age / ${n(get(w, "interesting", "momentum", "recency_half_life_days"))} days)
Novelty = pct(distance from crowded clusters) x pct(cluster funding growth over ${n(get(w, "interesting", "novelty", "cluster_growth_window_quarters"))} quarters)`}</Formula>
        <h3 className="font-medium">Fit (F) and final score</h3>
        <Formula>{`F = max(${lenses}) against the upGrad gap map; fit type = the best category
Shrinkage: s = (n x s_raw + ${n(get(w, "shrinkage", "k"))} x pool mean) / (n + ${n(get(w, "shrinkage", "k"))}), n = verified signals
Confidence C = verified claims / expected claims, capped at 1
Final = T^${n(ex.T)} x I^${n(ex.I)} x F^${n(ex.F)} x C^${n(ex.C)}  (each factor floored at ${n(get(w, "final", "epsilon"))})
India first: Final x ${n(get(w, "final", "region_multiplier", "india"))} if located in India, x ${n(get(w, "final", "region_multiplier", "unknown"))} if unverified, x ${n(get(w, "final", "region_multiplier", "outside"))} if outside India
Final after judge = ${n(get(w, "pairwise", "blend", "final"))} x Final + ${n(get(w, "pairwise", "blend", "bt_pct"))} x pct(Bradley-Terry), top ${n(get(w, "pairwise", "top_n"))}, both orders
Shortlist: MMR with lambda ${n(get(w, "mmr", "lambda"))}, ${n(get(w, "mmr", "shortlist_max"))} companies, at least ${n(get(w, "mmr", "min_per_fit_type"))} per fit type and at least ${n(get(w, "mmr", "min_india"))} located in India`}</Formula>
        <h3 className="font-medium">Size, stage and location rules</h3>
        <ul className="list-disc pl-5 text-sm grid gap-1">
          <li>Size uses the largest verified figure: corroborated rounds, a stated total, or the largest single round. Unknown size multiplies Acquire by {n(get(w, "fit", "acquire", "unknown_size_factor"))}; above the ceiling Acquire is 0.</li>
          <li>Very early companies (under ${Number(get(w, "fit", "co_build", "very_early_max_raised_usd") ?? 0).toLocaleString("en-US")} raised, pre-seed or seed, or at most {n(get(w, "fit", "co_build", "very_early_max_age_years"))} years old) are Co-build fits; Acquire is multiplied by {n(get(w, "fit", "acquire", "very_early_factor"))}.</li>
          <li>Location comes from verified claims, strongest first: a country or city claim, an Indian registry listing, an evidence quote such as &ldquo;Bengaluru-based&rdquo;, an amount stated in INR, then a .in domain.</li>
          <li>Inactive needs combined evidence (dead website and no activity in 12 months, or founder no longer listed and old activity). One weak signal gives &ldquo;unknown&rdquo;, which lowers confidence instead of excluding.</li>
        </ul>
      </section>

      <div className="grid lg:grid-cols-2 gap-6">
        <section className="card p-5">
          <h2 className="text-lg font-semibold mb-2">upGrad gap map used</h2>
          <h3 className="text-sm font-medium mt-2">Business units</h3>
          <ul className="list-disc pl-5 text-sm">{gap.business_units?.map((b) => <li key={b.id}>{b.name}</li>)}</ul>
          <h3 className="text-sm font-medium mt-3">Wanted capabilities</h3>
          <ul className="list-disc pl-5 text-sm">{gap.wanted_capabilities?.map((c) => <li key={c.id}>{c.name}</li>)}</ul>
        </section>
        <section className="card p-5 grid gap-3 content-start">
          <h2 className="text-lg font-semibold">Gates and exclusions</h2>
          <p className="text-sm">Shortlisted only if education-relevant, not inactive, founder identity verified by a non-LinkedIn source, current role and company existence verified, and at least three verified track-record or interest claims.</p>
          <p className="text-sm"><span className="font-medium">Competitors at scale:</span> {excl.competitors_at_scale?.join(", ") || "-"}</p>
          <p className="text-sm"><span className="font-medium">Size ceiling:</span> raised over ${Number(get(excl, "size_ceiling", "max_total_raised_usd") ?? 0).toLocaleString("en-US")} or more than {Number(get(excl, "size_ceiling", "max_headcount") ?? 0).toLocaleString("en-US")} employees.</p>
          <h2 className="text-lg font-semibold mt-2">Sources and the LinkedIn policy</h2>
          <p className="text-sm">News feeds, GDELT, accelerator and investor portfolio pages, curated lists, GitHub, Hacker News, BetaList, company websites, Tofler and Zauba summaries. LinkedIn, Crunchbase and Tracxn are never fetched: only search-engine snippets are used, and they need a non-LinkedIn source to count toward the shortlist. Every crawler request respects robots.txt and rate limits.</p>
          <p className="text-sm"><span className="font-medium">Models:</span> {Object.entries(run.config_snapshot.models).map(([k, v]) => `${k} ${v}`).join(", ")}</p>
        </section>
      </div>
    </div>
  );
}

export default function MethodologyPage() {
  return (
    <>
      <PageHeader title="Methodology" subtitle="How every ranking is produced, with the exact weights of the latest published run." />
      <Suspense fallback={<Skeleton className="h-[600px]" />}>
        <Content />
      </Suspense>
    </>
  );
}
