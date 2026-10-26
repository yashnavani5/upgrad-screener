import Link from "next/link";

import { day } from "@/lib/format";
import { getRun } from "@/lib/queries";

/** Shown when a page is pinned to a specific run (?run=...). */
export async function RunBanner({ run, path }: { run?: string; path: string }) {
  if (!run) return null;
  const r = await getRun(run);
  return (
    <div className="card border-info bg-info-subtle px-4 py-3 mb-6 text-sm flex flex-wrap items-center gap-2" role="note">
      <span>
        Viewing run <span className="font-mono">{run.slice(0, 8)}</span>
        {r ? ` from ${day(r.started_at)} (${r.status})` : " (not found)"}.
      </span>
      <Link href={path} className="link font-medium">Back to the latest run</Link>
    </div>
  );
}
