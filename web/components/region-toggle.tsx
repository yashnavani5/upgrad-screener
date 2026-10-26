import { Globe2, MapPin } from "lucide-react";
import Link from "next/link";

import type { RegionFilter } from "@/lib/region";

/** India / Global switch. Plain links (shareable URLs, works without JavaScript); other query params are kept. */
export function RegionToggle({ region, path, params, counts }: {
  region: RegionFilter; path: string; params: Record<string, string | undefined>; counts: { india: number; global: number };
}) {
  const href = (r: RegionFilter) => {
    const q = new URLSearchParams(Object.entries({ ...params, region: r }).filter(([, v]) => v) as [string, string][]);
    return `${path}?${q.toString()}`;
  };
  const item = (r: RegionFilter, label: string, icon: React.ReactNode, n: number) => (
    <Link href={href(r)} aria-current={region === r ? "page" : undefined} data-testid={`region-${r}`} scroll={false}
          className={`h-9 px-3 inline-flex items-center gap-1.5 text-sm ${r === "global" ? "border-l border-border-strong" : ""} ${
            region === r ? "bg-brand-subtle text-brand-hover font-medium" : "bg-surface text-ink hover:bg-surface-alt"}`}>
      {icon}{label}<span className="tabular text-ink-muted">({n})</span>
    </Link>
  );
  return (
    <nav aria-label="Region" className="inline-flex rounded-control border border-border-strong overflow-hidden w-fit">
      {item("india", "India", <MapPin className="w-4 h-4" aria-hidden />, counts.india)}
      {item("global", "Global", <Globe2 className="w-4 h-4" aria-hidden />, counts.global)}
    </nav>
  );
}
