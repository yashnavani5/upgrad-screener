"use client";

import clsx from "clsx";
import { createContext, useContext, type ReactNode } from "react";
import useSWR from "swr";

// Fit-category labels come from the active AI Policy (renamed or added categories show everywhere).
const DEFAULT_LABELS: Record<string, string> = { acquire: "Acquire", partner: "Partner", co_build: "Co-build" };
const COLORS: Record<string, string> = {
  acquire: "bg-fit-acquire-subtle text-fit-acquire",
  partner: "bg-fit-partner-subtle text-fit-partner",
  co_build: "bg-fit-cobuild-subtle text-fit-cobuild",
};
type Labels = { labels: Record<string, string>; enabled: string[] };
const DEFAULTS: Labels = { labels: DEFAULT_LABELS, enabled: Object.keys(DEFAULT_LABELS) };
const LabelsContext = createContext<Labels>(DEFAULTS);

export function FitLabelsProvider({ children }: { children: ReactNode }) {
  const { data } = useSWR<Labels>("/api/policy/labels", (u: string) => fetch(u).then((r) => r.json()), { revalidateOnFocus: true });
  const value = data?.labels ? { labels: { ...DEFAULT_LABELS, ...data.labels }, enabled: data.enabled ?? DEFAULTS.enabled } : DEFAULTS;
  return <LabelsContext.Provider value={value}>{children}</LabelsContext.Provider>;
}

/** Names of the active policy's categories, plus the ids to offer in filters: enabled categories first, then any
 *  other fit types present in `present` (older runs, disabled categories). */
export function useFitLabels(present: (string | null | undefined)[] = []): Labels & { ids: string[] } {
  const ctx = useContext(LabelsContext);
  const ids = [...new Set([...ctx.enabled, ...present.filter((x): x is string => !!x)])];
  return { ...ctx, ids };
}

export function fitLabel(fit: string, labels: Record<string, string>): string {
  return labels[fit] ?? fit.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function FitBadge({ fit }: { fit: string | null | undefined }) {
  const { labels } = useContext(LabelsContext);
  if (!fit) return <span className="chip bg-surface-alt text-ink-muted">No fit</span>;
  return <span className={clsx("chip", COLORS[fit] ?? "bg-info-subtle text-info")} data-fit={fit}>{fitLabel(fit, labels)}</span>;
}
