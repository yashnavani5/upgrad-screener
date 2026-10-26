import clsx from "clsx";
import { Check, Loader2, Minus, X } from "lucide-react";

import { STAGES, STAGE_LABEL, clean } from "@/lib/format";
import type { Run } from "@/lib/schemas";

type CP = { status?: string; summary?: string | null };

/** Discover -> Resolve -> Enrich -> Verify -> Score -> Explain -> Publish, from the run's checkpoints. */
export function StageStepper({ run }: { run: Run }) {
  const cps = (run.checkpoints ?? {}) as Record<string, CP>;
  const running = run.status === "running" || run.status === "queued";
  return (
    <ol className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2" aria-label="Pipeline stages">
      {STAGES.map((stage) => {
        const cp = cps[stage];
        const isCurrent = running && run.current_stage === stage;
        const status = isCurrent ? "running" : cp?.status ?? "pending";
        const Icon = status === "done" ? Check : status === "running" ? Loader2 : status === "failed" ? X : Minus;
        return (
          <li key={stage} className={clsx("rounded-control border p-3 flex flex-col gap-1 min-w-0",
            status === "done" && "border-success bg-success-subtle",
            status === "running" && "border-brand bg-brand-subtle",
            status === "partial" && "border-warning bg-warning-subtle",
            status === "failed" && "border-danger bg-danger-subtle",
            ["pending", "skipped"].includes(status) && "border-border bg-surface")}
              title={clean(cp?.summary) || STAGE_LABEL[stage]} data-stage={stage} data-status={status}>
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <Icon className={clsx("w-4 h-4 shrink-0", status === "running" && "animate-spin")} aria-hidden />
              {STAGE_LABEL[stage]}
            </span>
            <span className="text-xs text-ink-muted">
              {status === "running" ? `${Math.round((run.stage_progress ?? 0) * 100)}%` : status === "pending" ? "Waiting" : status}
            </span>
            {isCurrent && (
              <span className="h-1 rounded-full bg-border overflow-hidden" aria-hidden>
                <span className="block h-full bg-brand" style={{ width: `${Math.round((run.stage_progress ?? 0) * 100)}%` }} />
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
