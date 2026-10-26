import clsx from "clsx";
import { AlertTriangle, ExternalLink, Inbox } from "lucide-react";
import type { ReactNode } from "react";

import { REASON_LABEL, clean, domain, score } from "@/lib/format";
import { REGION_LABEL } from "@/lib/region";

export { FitBadge } from "./fit-badge";

export function ReasonBadge({ code }: { code: string }) {
  const tone = ["COMPETITOR", "UPGRAD_OWNED", "TOO_LARGE"].includes(code)
    ? "bg-info-subtle text-info"
    : ["INACTIVE", "NOT_EDUCATION"].includes(code)
      ? "bg-danger-subtle text-danger"
      : code === "UNVERIFIABLE"
        ? "bg-warning-subtle text-warning"
        : "bg-brand-subtle text-brand-hover";
  return <span className={clsx("chip", tone)} title={code}>{REASON_LABEL[code] ?? code}</span>;
}

const STATUS_TONE: Record<string, string> = {
  succeeded: "bg-success-subtle text-success",
  partial: "bg-warning-subtle text-warning",
  failed: "bg-danger-subtle text-danger",
  running: "bg-brand-subtle text-brand-hover",
  queued: "bg-info-subtle text-info",
  active: "bg-success-subtle text-success",
  inactive: "bg-danger-subtle text-danger",
  unknown: "bg-surface-alt text-ink-muted",
  done: "bg-success-subtle text-success",
  skipped: "bg-surface-alt text-ink-muted",
};

export function StatusPill({ status, label }: { status: string; label?: string }) {
  return (
    <span className={clsx("chip rounded-full px-2.5", STATUS_TONE[status] ?? "bg-surface-alt text-ink-muted")} data-status={status}>
      {status === "running" && <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse" aria-hidden />}
      {label ?? status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

export function ScoreBar({ label, value, emphasis = false, hint, labelWidth = "w-10" }: {
  label: string; value: number | null | undefined; emphasis?: boolean; hint?: string; labelWidth?: string;
}) {
  const pct = Math.max(0, Math.min(1, value ?? 0)) * 100;
  return (
    <div className="flex items-center gap-2 text-xs" title={`${label}: ${score(value)}${hint ? `. ${hint}` : ""}`}>
      <span className={clsx(labelWidth, "shrink-0", emphasis ? "font-semibold text-ink" : "text-ink-muted")}>{label}</span>
      <div className="flex-1 h-2 rounded-full bg-surface-alt overflow-hidden" role="meter" aria-label={label}
           aria-valuenow={Number(score(value)) || 0} aria-valuemin={0} aria-valuemax={1}>
        <div className={clsx("h-full rounded-full", emphasis ? "bg-brand-strong" : "bg-brand")} style={{ width: `${pct}%` }} />
      </div>
      <span className={clsx("w-10 text-right tabular", emphasis && "font-semibold")}>{score(value)}</span>
    </div>
  );
}

export function EvidenceLink({ url, children }: { url: string; children?: ReactNode }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="link inline-flex items-center gap-1 text-sm break-all">
      {children ?? domain(url)}
      <ExternalLink className="w-3 h-3 shrink-0" aria-hidden />
    </a>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card p-8 text-center flex flex-col items-center gap-2" role="status">
      <Inbox className="w-8 h-8 text-ink-muted" aria-hidden />
      <p className="font-semibold">{title}</p>
      {children && <div className="text-sm text-ink-muted max-w-prose">{children}</div>}
    </div>
  );
}

export function ErrorState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="card p-6 flex items-start gap-3 border-danger" role="alert">
      <AlertTriangle className="w-5 h-5 text-danger shrink-0 mt-0.5" aria-hidden />
      <div>
        <p className="font-semibold text-danger">{title}</p>
        {detail && <p className="text-sm text-ink-muted mt-1">{clean(detail)}</p>}
      </div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("rounded-card bg-border/60 animate-pulse", className)} aria-hidden />;
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row md:items-end gap-3 mb-6">
      <div className="min-w-0">
        <h1 className="text-xl md:text-2xl font-semibold text-ink">{title}</h1>
        {subtitle && <p className="text-sm text-ink-muted mt-1">{subtitle}</p>}
      </div>
      {children && <div className="md:ml-auto flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}

/** Verified location: India / Outside India / Location unverified (hover shows the evidence used). */
export function RegionBadge({ region, basis }: { region?: string | null; basis?: string | null }) {
  const cls = region === "india" ? "bg-success-subtle text-success" : region === "outside" ? "bg-surface-alt text-ink" : "bg-surface-alt text-ink-muted";
  return (
    <span className={clsx("chip", cls)} data-region={region ?? "unknown"} title={basis ? `Location evidence: ${clean(basis)}` : undefined}>
      {REGION_LABEL[region ?? "unknown"] ?? region}
    </span>
  );
}
