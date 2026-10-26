"use client";

import { fitLabel, useFitLabels } from "@/components/fit-badge";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const STAGE: Record<string, string> = {
  discovered: "Discovered", resolved: "Resolved", enriched: "Enriched", verified: "Verified",
  gated: "Passed gates", scored: "Scored", shortlisted: "Shortlisted",
};

// Every resolved company is scored; the gate then narrows it. Show scored before gated so the funnel only narrows.
const ORDER = ["discovered", "resolved", "enriched", "verified", "scored", "gated", "shortlisted"];

export function FunnelChart({ rows: raw }: { rows: { stage: string; count: number }[] }) {
  const rows = [...raw].sort((a, b) => ORDER.indexOf(a.stage) - ORDER.indexOf(b.stage));
  const data = rows.map((r, i) => ({
    stage: STAGE[r.stage] ?? r.stage,
    count: r.count,
    drop: i > 0 && rows[i - 1].count ? Math.round((1 - r.count / rows[i - 1].count) * 100) : null,
  }));
  return (
    <div className="h-72" data-testid="funnel-chart" role="img" aria-label={`Funnel: ${data.map((d) => `${d.stage} ${d.count}`).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 48, top: 4, bottom: 4 }}>
          <CartesianGrid horizontal={false} stroke="var(--border)" />
          <XAxis type="number" tick={{ fill: "var(--ink-muted)", fontSize: 12 }} stroke="var(--border)" />
          <YAxis type="category" dataKey="stage" width={110} tick={{ fill: "var(--ink)", fontSize: 12 }} stroke="var(--border)" />
          <Tooltip cursor={{ fill: "var(--surface-alt)" }}
                   formatter={(v: number, _n, p) => [`${v}${p.payload.drop !== null ? ` (${p.payload.drop}% drop)` : ""}`, "Count"]} />
          <Bar dataKey="count" fill="var(--brand)" radius={[0, 4, 4, 0]} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" fill="var(--ink)" fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const FIT_COLOR: Record<string, string> = { acquire: "var(--fit-acquire)", partner: "var(--fit-partner)", co_build: "var(--fit-cobuild)" };
export function FitSplit({ rows }: { rows: { fit: string; count: number }[] }) {
  const { labels } = useFitLabels();
  const data = rows.map((r) => ({ fit: fitLabel(r.fit, labels), key: r.fit, count: r.count }));
  return (
    <div className="h-56" data-testid="fit-chart" role="img" aria-label={`Fit split: ${data.map((d) => `${d.fit} ${d.count}`).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ left: 0, right: 8, top: 16, bottom: 4 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" />
          <XAxis dataKey="fit" tick={{ fill: "var(--ink)", fontSize: 12 }} stroke="var(--border)" />
          <YAxis allowDecimals={false} tick={{ fill: "var(--ink-muted)", fontSize: 12 }} stroke="var(--border)" width={32} />
          <Tooltip cursor={{ fill: "var(--surface-alt)" }} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]} isAnimationActive={false}>
            {data.map((d) => <Cell key={d.key} fill={FIT_COLOR[d.key] ?? "var(--info)"} />)}
            <LabelList dataKey="count" position="top" fill="var(--ink)" fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
