// India-first region filter shared by the list pages and the CSV export (?region=india | global).
export type RegionFilter = "india" | "global";

export const REGION_LABEL: Record<string, string> = { india: "India", outside: "Outside India", unknown: "Location unverified" };

/** India is the default view; "global" shows every company. */
export function parseRegion(value: string | string[] | null | undefined): RegionFilter {
  return value === "global" ? "global" : "india";
}

export function inRegion(row: { region?: string | null }, filter: RegionFilter): boolean {
  return filter === "global" || row.region === "india";
}
