import type { Metadata } from "next";
import { Suspense } from "react";

import { RegionToggle } from "@/components/region-toggle";
import { RunBanner } from "@/components/run-banner";
import { ShortlistView } from "@/components/shortlist-view";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { getShortlist } from "@/lib/queries";
import { inRegion, parseRegion } from "@/lib/region";

export const metadata: Metadata = { title: "Shortlist" };

async function Data({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  const { run, region: regionParam } = await searchParams;
  const region = parseRegion(regionParam);
  const rows = await getShortlist(run);
  const shown = rows.filter((r) => inRegion(r, region));
  const toggle = <RegionToggle region={region} path="/shortlist" params={{ run }}
                               counts={{ india: rows.filter((r) => inRegion(r, "india")).length, global: rows.length }} />;
  return (
    <>
      <RunBanner run={run} path="/shortlist" />
      {rows.length === 0 ? (
        <EmptyState title="No shortlist for this run">The shortlist is written when a run finishes scoring and explanation.</EmptyState>
      ) : (
        <div className="grid gap-4">
          {toggle}
          {shown.length === 0
            ? <EmptyState title="No India-located companies in this shortlist">Switch to Global to see every pick.</EmptyState>
            : <ShortlistView rows={shown} run={run} region={region} />}
        </div>
      )}
    </>
  );
}

export default function ShortlistPage({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  return (
    <>
      <PageHeader title="Shortlist" subtitle="India first: ranked by Final score after the pairwise judge, selected for diversity (MMR), with at least 10 India-located picks when the pool allows." />
      <Suspense fallback={<div className="grid md:grid-cols-2 gap-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-80" />)}</div>}>
        <Data searchParams={searchParams} />
      </Suspense>
    </>
  );
}
