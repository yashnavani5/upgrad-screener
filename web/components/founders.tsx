import { BadgeCheck, GraduationCap, Briefcase, Trophy } from "lucide-react";

import { clean } from "@/lib/format";

type Fact = { field: "prior_role" | "prior_exit" | "education"; value: Record<string, unknown>; source_url: string };
export type FounderView = {
  name: string; role?: string | null; title?: string | null; linkedin_url?: string | null; x_url?: string | null;
  identity_verified?: boolean; background?: Fact[]; line?: string | null; why?: string | null; fit?: string | null;
};

const ICON = { prior_role: Briefcase, prior_exit: Trophy, education: GraduationCap } as const;

/** Code-built founder lines start with "Name: "; the founder's name is already shown above them. */
export function founderText(name: string, text?: string | null): string {
  const t = clean(text ?? "");
  return t.startsWith(`${name}: `) ? t.slice(name.length + 2) : t;
}

function factText(f: Fact): string {
  const v = f.value as Record<string, string | number | null | undefined>;
  if (f.field === "education") return `${v.institution}${v.degree ? `, ${v.degree}` : ""}`;
  if (f.field === "prior_exit") return `Exit: ${v.company}${v.acquirer ? ` (acquired by ${v.acquirer})` : ""}`;
  const years = [v.start_year, v.end_year].filter(Boolean).join("-");
  return `${v.role ? `${v.role}, ` : ""}${v.company}${years ? ` ${years}` : ""}`;
}

/** Unique background facts (the same role is often stated by several sources), exits first, then roles, then education. */
function facts(bg: Fact[] = [], max = 5): Fact[] {
  const order = { prior_exit: 0, prior_role: 1, education: 2 };
  const seen = new Set<string>();
  return [...bg].sort((a, b) => order[a.field] - order[b.field]).filter((f) => {
    const k = `${f.field}:${factText(f).toLowerCase()}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, max);
}

export function FounderList({ founders, compact = false }: { founders: FounderView[]; compact?: boolean }) {
  if (!founders.length) return <p className="text-sm text-ink-muted">No founder verified yet.</p>;
  return (
    <ul className="grid gap-3" data-testid="founder-list">
      {founders.map((f) => {
        const bg = facts(f.background, compact ? 3 : 6);
        return (
          <li key={f.name} className="grid gap-1" data-testid="founder">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-semibold">{f.name}</span>
              {f.identity_verified && (
                <span title="Identity verified by a non-social source (not LinkedIn or X alone)" className="inline-flex text-success">
                  <BadgeCheck className="w-4 h-4" aria-label="identity verified" />
                </span>
              )}
              {(f.title || f.role) && <span className="text-sm text-ink-muted">{clean(f.title || f.role)}</span>}
              <span className="flex gap-2 text-xs">
                {f.linkedin_url && <a className="link" href={f.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn</a>}
                {f.x_url && <a className="link" href={f.x_url} target="_blank" rel="noopener noreferrer" data-testid="founder-x">X</a>}
              </span>
            </div>
            {f.line && <p className="text-sm text-ink" data-testid="founder-line">{founderText(f.name, f.line)}</p>}
            {(f.why || f.fit) && (
              <dl className="grid gap-1 text-sm" data-testid="founder-why-fit">
                {f.why && <div><dt className="inline font-medium">Why they made the cut: </dt><dd className="inline text-ink-muted">{founderText(f.name, f.why)}</dd></div>}
                {f.fit && <div><dt className="inline font-medium">How they fit upGrad: </dt><dd className="inline text-ink-muted">{founderText(f.name, f.fit)}</dd></div>}
              </dl>
            )}
            {bg.length > 0 && (
              <ul className="flex flex-wrap gap-1.5" aria-label={`${f.name} verified background`}>
                {bg.map((b) => {
                  const Icon = ICON[b.field];
                  return (
                    <li key={`${b.field}-${factText(b)}`}>
                      <a href={b.source_url} target="_blank" rel="noopener noreferrer" title="Open the source"
                         className="chip bg-surface-alt text-ink hover:bg-brand-subtle hover:text-brand-hover transition-colors max-w-[260px]">
                        <Icon className="w-3 h-3 shrink-0" aria-hidden /><span className="truncate">{clean(factText(b))}</span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
            {!f.line && !f.why && !bg.length && <p className="text-xs text-ink-muted">No verified background beyond the founding role yet.</p>}
          </li>
        );
      })}
    </ul>
  );
}
