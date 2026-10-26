import "server-only";

import { getPolicyVersions } from "./queries";

// Fit-category names of the active AI Policy version. Defaults apply when no version is saved; ids of categories
// that were removed or disabled keep a readable name so older runs still display.
export const DEFAULT_FIT_LABELS: Record<string, string> = { acquire: "Acquire", partner: "Partner", co_build: "Co-build" };

export type FitLabels = { version: number | null; labels: Record<string, string>; enabled: string[] };

export async function activeFitLabels(): Promise<FitLabels> {
  try {
    const active = (await getPolicyVersions()).find((v) => v.status === "active");
    if (!active) return { version: null, labels: { ...DEFAULT_FIT_LABELS }, enabled: Object.keys(DEFAULT_FIT_LABELS) };
    const cats = (active.policy.categories ?? []) as { id: string; label: string; enabled?: boolean }[];
    return {
      version: active.version,
      labels: { ...DEFAULT_FIT_LABELS, ...Object.fromEntries(cats.map((c) => [c.id, c.label])) },
      enabled: cats.filter((c) => c.enabled !== false).map((c) => c.id),
    };
  } catch {
    return { version: null, labels: { ...DEFAULT_FIT_LABELS }, enabled: Object.keys(DEFAULT_FIT_LABELS) };
  }
}

export function fitName(fit: string | null | undefined, labels: Record<string, string>): string {
  if (!fit) return "";
  return labels[fit] ?? fit.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
