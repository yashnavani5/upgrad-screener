"use client";

import * as Tooltip from "@radix-ui/react-tooltip";
import { Quote } from "lucide-react";
import { useMemo, useState } from "react";

import { clean, day, domain, score } from "@/lib/format";

type ClaimRow = { field: string; value?: unknown; source_url: string; evidence_span: string; retrieved_at: string; verifier_confidence?: number | null };

function show(field: string, value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value !== "object") return String(value);
  const v = value as Record<string, unknown>;
  const pick = ["text", "name", "city", "country", "year", "tag", "label", "role", "company", "stage", "fiscal_year", "date", "count", "stars", "institution", "acquirer", "url"];
  const parts = pick.filter((k) => v[k] !== null && v[k] !== undefined && v[k] !== "" && !(k === "tag" && v.label)).map((k) => String(v[k]));
  if (typeof v.amount_usd === "number") parts.push(`$${Math.round(v.amount_usd).toLocaleString("en-US")}`);
  if (Array.isArray(v.investors) && v.investors.length) parts.push(`investors: ${v.investors.join(", ")}`);
  return clean(parts.join(", ")) || field;
}

export function ClaimsTable({ claims }: { claims: ClaimRow[] }) {
  const [field, setField] = useState("all");
  const fields = useMemo(() => [...new Set(claims.map((c) => c.field))].sort(), [claims]);
  const rows = field === "all" ? claims : claims.filter((c) => c.field === field);
  return (
    <Tooltip.Provider delayDuration={150}>
      <div className="flex items-center gap-2 mb-3">
        <label htmlFor="claim-field" className="text-sm text-ink-muted">Field</label>
        <select id="claim-field" className="input h-9" value={field} onChange={(e) => setField(e.target.value)} data-testid="claim-filter">
          <option value="all">All ({claims.length})</option>
          {fields.map((f) => <option key={f} value={f}>{f} ({claims.filter((c) => c.field === f).length})</option>)}
        </select>
      </div>
      {/* Phones: stacked cards with the quote inline (there is no hover on touch screens). */}
      <ul className="sm:hidden grid gap-3" data-testid="claims-list">
        {rows.map((c, i) => (
          <li key={`${c.source_url}-${c.field}-${i}`} className="border border-border rounded-control p-3 grid gap-1.5 text-sm" data-testid="claim-card">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium">{c.field}</span>
              <span className="text-xs text-ink-muted tabular">{score(c.verifier_confidence)}, {day(c.retrieved_at)}</span>
            </div>
            <div>{show(c.field, c.value)}</div>
            <blockquote className="text-ink-muted border-l-2 border-brand pl-2" data-testid="claim-quote">&ldquo;{clean(c.evidence_span)}&rdquo;</blockquote>
            <a className="link break-all" href={c.source_url} target="_blank" rel="noopener noreferrer">{domain(c.source_url)}</a>
          </li>
        ))}
      </ul>
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full min-w-[760px]" data-testid="claims-table">
          <thead className="bg-surface-alt"><tr>
            <th className="th">Field</th><th className="th">Value</th><th className="th">Confidence</th><th className="th">Retrieved</th><th className="th">Source</th><th className="th"><span className="sr-only">Quote</span></th>
          </tr></thead>
          <tbody>
            {rows.map((c, i) => (
              <tr key={`${c.source_url}-${c.field}-${i}`} className="border-t border-border">
                <td className="td font-medium whitespace-nowrap">{c.field}</td>
                <td className="td">{show(c.field, c.value)}</td>
                <td className="td tabular">{score(c.verifier_confidence)}</td>
                <td className="td whitespace-nowrap">{day(c.retrieved_at)}</td>
                <td className="td"><a className="link break-all" href={c.source_url} target="_blank" rel="noopener noreferrer">{domain(c.source_url)}</a></td>
                <td className="td">
                  <Tooltip.Root>
                    <Tooltip.Trigger asChild>
                      <button type="button" className="btn-ghost h-8 px-2" aria-label={`Evidence quote: ${clean(c.evidence_span)}`} data-testid="quote">
                        <Quote className="w-4 h-4" aria-hidden />
                      </button>
                    </Tooltip.Trigger>
                    <Tooltip.Portal>
                      <Tooltip.Content side="left" sideOffset={6} className="card shadow-pop max-w-sm p-3 text-sm z-50" data-testid="quote-tip">
                        &ldquo;{clean(c.evidence_span)}&rdquo;
                        <div className="text-xs text-ink-muted mt-2">{domain(c.source_url)}</div>
                        <Tooltip.Arrow className="fill-[var(--surface)]" />
                      </Tooltip.Content>
                    </Tooltip.Portal>
                  </Tooltip.Root>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Tooltip.Provider>
  );
}
