"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { REASON_LABEL, clean } from "@/lib/format";
import type { LeftOut } from "@/lib/schemas";

import { EmptyState, EvidenceLink, ReasonBadge, RegionBadge } from "./ui";

const PAGE = 25;

export function LeftOutView({ rows }: { rows: LeftOut[] }) {
  const [reason, setReason] = useState("all");
  const [sort, setSort] = useState<"rank" | "name">("rank");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const reasons = useMemo(() => [...new Set(rows.map((r) => r.reason_code))].sort(), [rows]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const f = rows.filter((r) => (reason === "all" || r.reason_code === reason)
      && (!q || `${r.company} ${r.founders.map((x) => x.name).join(" ")}`.toLowerCase().includes(q)));
    return [...f].sort((a, b) => sort === "name" ? a.company.localeCompare(b.company)
      : (a.would_have_ranked ?? 1e9) - (b.would_have_ranked ?? 1e9));
  }, [rows, reason, sort, query]);
  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const current = Math.min(page, pages - 1);
  const visible = shown.slice(current * PAGE, current * PAGE + PAGE);
  return (
    <div className="grid gap-4">
      <div className="card p-4 flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-muted">Reason</span>
          <select className="input h-9" value={reason} onChange={(e) => { setReason(e.target.value); setPage(0); }} data-testid="reason-filter">
            <option value="all">All reasons ({rows.length})</option>
            {reasons.map((r) => <option key={r} value={r}>{REASON_LABEL[r] ?? r} ({rows.filter((x) => x.reason_code === r).length})</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-muted">Sort by</span>
          <select className="input h-9" value={sort} onChange={(e) => { setSort(e.target.value as "rank" | "name"); setPage(0); }} data-testid="left-out-sort">
            <option value="rank">Would-have-ranked</option>
            <option value="name">Company name</option>
          </select>
        </label>
        <input className="input h-9 w-full sm:w-64" placeholder="Search company or founder" value={query} aria-label="Search"
               onChange={(e) => { setQuery(e.target.value); setPage(0); }} data-testid="left-out-search" />
        <span className="sm:ml-auto text-sm text-ink-muted" data-testid="left-out-count">{shown.length} companies</span>
      </div>
      {shown.length === 0 ? <EmptyState title="Nothing left out for this filter" /> : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[820px]" data-testid="left-out-table">
            <thead className="bg-surface-alt"><tr>
              <th className="th">Would rank</th><th className="th">Company</th><th className="th">Founders</th><th className="th">Region</th><th className="th">Reason</th><th className="th">Explanation</th><th className="th">Source</th>
            </tr></thead>
            <tbody>
              {visible.map((r) => (
                <tr key={r.entity_id} className="border-t border-border">
                  <td className="td tabular">{r.would_have_ranked ? `#${r.would_have_ranked}` : "-"}</td>
                  <td className="td font-medium"><Link className="hover:text-brand-strong" href={`/company/${r.entity_id}`}>{r.company}</Link></td>
                  <td className="td">{r.founders.map((f) => f.name).join(", ") || "-"}</td>
                  <td className="td"><RegionBadge region={r.region} /></td>
                  <td className="td"><ReasonBadge code={r.reason_code} /></td>
                  <td className="td text-ink-muted max-w-md">{clean(r.explanation)}</td>
                  <td className="td">{r.source_url ? <EvidenceLink url={r.source_url} /> : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <div className="flex items-center justify-end gap-3 text-sm">
          <button type="button" className="btn-secondary h-9" onClick={() => setPage(current - 1)} disabled={current === 0} data-testid="left-out-prev">Previous</button>
          <span className="tabular text-ink-muted">Page {current + 1} of {pages}</span>
          <button type="button" className="btn-secondary h-9" onClick={() => setPage(current + 1)} disabled={current >= pages - 1} data-testid="left-out-next">Next</button>
        </div>
      )}
    </div>
  );
}
