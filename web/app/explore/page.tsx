import type { Metadata } from "next";
import { Suspense } from "react";

import { ExploreTable } from "@/components/explore-table";
import { RegionToggle } from "@/components/region-toggle";
import { RunBanner } from "@/components/run-banner";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { getLongList } from "@/lib/queries";
import { inRegion, parseRegion } from "@/lib/region";

export const metadata: Metadata = { title: "Explore" };

async function Data({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  const { run, region: regionParam } = await searchParams;
  const region = parseRegion(regionParam);
  const rows = await getLongList(run);
  const shown = rows.filter((r) => inRegion(r, region));
  const toggle = <RegionToggle region={region} path="/explore" params={{ run }}
                               counts={{ india: rows.filter((r) => inRegion(r, "india")).length, global: rows.length }} />;
  return (
    <>
      <RunBanner run={run} path="/explore" />
      {rows.length === 0 ? <EmptyState title="No scored companies for this run" /> : (
        <div className="grid gap-4">
          {toggle}
          {shown.length === 0 ? <EmptyState title="No India-located companies in this list" /> : <ExploreTable rows={shown} />}
        </div>
      )}
    </>
  );
}

export default function ExplorePage({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  return (
    <>
      <PageHeader title="Explore" subtitle="The long list: top 200 of the run with every sub-score. Click a row for the full dossier." />
      <Suspense fallback={<Skeleton className="h-[480px]" />}>
        <Data searchParams={searchParams} />
      </Suspense>
    </>
  );
}
