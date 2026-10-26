import type { Metadata } from "next";
import { Suspense } from "react";

import { CuratedLeftOut } from "@/components/curated-left-out";
import { LeftOutView } from "@/components/left-out-view";
import { RegionToggle } from "@/components/region-toggle";
import { RunBanner } from "@/components/run-banner";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { getLeftOut } from "@/lib/queries";
import { inRegion, parseRegion } from "@/lib/region";

export const metadata: Metadata = { title: "Left out" };

async function Data({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  const { run, region: regionParam } = await searchParams;
  const region = parseRegion(regionParam);
  const rows = await getLeftOut(run);
  const shown = rows.filter((r) => inRegion(r, region));
  const toggle = <RegionToggle region={region} path="/left-out" params={{ run }}
                               counts={{ india: rows.filter((r) => inRegion(r, "india")).length, global: rows.length }} />;
  return (
    <>
      <RunBanner run={run} path="/left-out" />
      {rows.length === 0 ? <EmptyState title="No exclusions for this run" /> : (
        <div className="grid gap-6">
          <CuratedLeftOut rows={rows} />
          <h2 className="text-lg font-semibold -mb-2">Everything else that was scored and left out</h2>
          {toggle}
          {shown.length === 0 ? <EmptyState title="No India-located companies left out" /> : <LeftOutView rows={shown} />}
        </div>
      )}
    </>
  );
}

export default function LeftOutPage({ searchParams }: { searchParams: Promise<{ run?: string; region?: string }> }) {
  return (
    <>
      <PageHeader title="Left out" subtitle="Companies that were scored but did not make the shortlist, with the reason and where they would have ranked." />
      <Suspense fallback={<Skeleton className="h-96" />}>
        <Data searchParams={searchParams} />
      </Suspense>
    </>
  );
}
