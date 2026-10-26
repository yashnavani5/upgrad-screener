"use client";

import { ArrowDownRight, ArrowUpRight, Eye, History, Loader2, Plus, RotateCcw, Save, Trash2, Undo2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";

import { day, time } from "@/lib/format";
import type { PolicyVersionRow } from "@/lib/schemas";

import { FitBadge } from "./fit-badge";
import { RegionBadge } from "./ui";

// --- policy shape (mirrors screener.policy.Policy) -------------------------------------------------------
type Item = { id: string; name: string };
type Rule = {
  capability_weight: number; segment_weight: number; both_weight: number; require_capabilities: string[];
  require_segments: string[]; min_raised_usd: number | null; max_raised_usd: number | null; unknown_size_factor: number;
  stage: { very_early: number; early: number; later: number }; founder_boost_min_t: number | null; founder_boost: number;
  founder_boost_early_only: boolean; use_geo: boolean;
};
type Category = { id: string; label: string; description: string; enabled: boolean; rule: Rule };
type Filters = {
  require_education: boolean; exclude_inactive: boolean; exclude_acquired: boolean; exclude_competitors: boolean;
  exclude_upgrad_owned: boolean; max_total_raised_usd: number | null; max_headcount: number | null;
  require_identity_verified: boolean; require_current_role: boolean; require_company_existence: boolean;
  min_track_record_claims: number; regions_allowed: Record<string, boolean>; founded_after: number | null;
  founded_before: number | null; min_total_raised_usd: number | null; require_verified_funding: boolean;
  excluded_stages: string[]; min_confidence: number; min_final_score: number; min_verified_claims: number;
};
type Weights = {
  track_record: Record<string, number>; team_depth_bonus: number; momentum: number; novelty: number;
  momentum_half_life_days: number; final_exponents: Record<string, number>; region_multiplier: Record<string, number>;
  judge_blend: number; shrinkage_k: number;
};
type Selection = { shortlist_size: number; min_per_category: number; min_india: number; diversity_lambda: number; judge_top_n: number };
type Lists = { competitors: string[]; upgrad_owned: string[]; blocklist: string[]; must_consider?: string[]; business_units: Item[]; wanted_capabilities: Item[] };
export type Policy = { categories: Category[]; filters: Filters; weights: Weights; selection: Selection; lists: Lists };

type Row = { rank: number; entity_id: string; company: string; fit_type: string; region: string; final: number };
type Side = { rows: Row[]; scored: number; eligible: number; excluded_by_reason: Record<string, number>;
  by_category: Record<string, number>; by_region: Record<string, number>; policy_filtered: { company: string; why: string }[] };
type Preview = { current: Side; draft: Side; entered: Row[]; left: Row[]; moved: (Row & { from: number })[]; note: string };

const TR_LABELS: Record<string, string> = {
  prior_exit: "Prior exit", prior_vc_backed: "Prior VC-backed company", top_investor: "Top investor",
  scale_pct_log: "Operating scale", years_in_edu_pct: "Years in education", operator_pedigree: "Operator pedigree",
  accelerator_or_award: "Accelerator or award",
};

// Plain-language help under each control: what it means, then what changing it does.
const TR_HINTS: Record<string, string> = {
  prior_exit: "A founder earlier built a company that was acquired or went public.",
  prior_vc_backed: "A founder's earlier company raised venture capital.",
  top_investor: "The current company is backed by a top-tier or edtech-specialist investor.",
  scale_pct_log: "Verified scale (revenue, learners, paying users), ranked against the other companies.",
  years_in_edu_pct: "Years the founder has worked in education, ranked against the other founders.",
  operator_pedigree: "A founder was a manager or above at a scaled edtech ($50M+ raised or 500+ staff).",
  accelerator_or_award: "A selective accelerator, a selective award, or notable open-source work (a popular GitHub project).",
};
const EXPONENT_HINTS: Record<string, string> = {
  T: "How much founder track record (T) moves the final score. Higher = bigger effect; 0 = ignored.",
  I: "How much 'interesting' (I: momentum and novelty) moves the final score. Higher = bigger effect; 0 = ignored.",
  F: "How much fit with upGrad (F) moves the final score. Higher = bigger effect; 0 = ignored.",
  C: "How much confidence (C: share of expected facts verified) moves the final score. Kept low so thin data lowers a score without killing it.",
};
const REGION_HINTS: Record<string, string> = {
  india: "Final-score multiplier for companies verified as located in India. 1 = no change.",
  unknown: "Multiplier when the location is not verified yet. 0.85 = a 15% lower final score until it is confirmed.",
  outside: "Multiplier for companies outside India. 0.6 = a 40% lower final score, so they need a clearly stronger case.",
};

// --- small controls -------------------------------------------------------------------------------------------
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-sm min-w-0">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-muted">{hint}</span>}
    </label>
  );
}

function Toggle({ label, hint, checked, onChange, testId }: { label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; testId?: string }) {
  return (
    <label className={`flex ${hint ? "items-start" : "items-center"} gap-3 text-sm cursor-pointer select-none min-w-0`}>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} data-testid={testId}
              className={`${hint ? "mt-0.5" : ""} relative w-10 h-6 rounded-full transition-colors shrink-0 ${checked ? "bg-brand-strong" : "bg-border-strong"}`}>
        <span className={`absolute left-0 top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-[18px]" : "translate-x-0.5"}`} />
      </button>
      <span className="grid min-w-0"><span className="font-medium">{label}</span>{hint && <span className="text-xs text-ink-muted">{hint}</span>}</span>
    </label>
  );
}

function Slider({ label, hint, value, onChange, min = 0, max = 1, step = 0.05, testId }: {
  label: string; hint?: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number; testId?: string;
}) {
  return (
    <div className="grid content-start gap-1 text-sm min-w-0">
      <div className="flex items-end justify-between gap-2 min-w-0 min-h-8">
        <span className="font-medium leading-tight min-w-0 break-words">{label}</span>
        <input type="number" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
               className="input h-8 w-[4.5rem] shrink-0 px-2 text-right tabular" aria-label={`${label} value`} />
      </div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
             className="w-full min-w-0 accent-[var(--brand)]" data-testid={testId} aria-label={label} />
      {hint && <span className="text-xs text-ink-muted">{hint}</span>}
    </div>
  );
}

function NumberBox({ label, hint, value, onChange, scale = 1, unit, allowEmpty = true, testId }: {
  label: string; hint?: string; value: number | null; onChange: (v: number | null) => void; scale?: number; unit?: string; allowEmpty?: boolean; testId?: string;
}) {
  return (
    <Field label={label} hint={hint}>
      <span className="flex items-center gap-2 min-w-0">
        <input type="number" className="input h-9 w-full max-w-40 min-w-0 tabular" value={value === null ? "" : value / scale} placeholder={allowEmpty ? "no limit" : ""}
               data-testid={testId} aria-label={label}
               onChange={(e) => onChange(e.target.value === "" ? (allowEmpty ? null : 0) : Number(e.target.value) * scale)} />
        {unit && <span className="text-ink-muted text-xs shrink-0">{unit}</span>}
      </span>
    </Field>
  );
}

function Tags({ label, hint, values, onChange, options, testId }: {
  label: string; hint?: string; values: string[]; onChange: (v: string[]) => void; options?: Item[]; testId?: string;
}) {
  const [text, setText] = useState("");
  const add = (v: string) => { const t = v.trim(); if (t && !values.includes(t)) onChange([...values, t]); setText(""); };
  return (
    <Field label={label} hint={hint}>
      <div className="flex flex-wrap gap-1.5" data-testid={testId}>
        {values.map((v) => (
          <span key={v} className="chip bg-surface-alt text-ink gap-1">
            {options?.find((o) => o.id === v)?.name ?? v}
            <button type="button" aria-label={`Remove ${v}`} className="text-ink-muted hover:text-danger" onClick={() => onChange(values.filter((x) => x !== v))}>x</button>
          </span>
        ))}
        {options ? (
          <select className="input h-8 text-xs max-w-full" value="" onChange={(e) => e.target.value && add(e.target.value)} aria-label={`Add to ${label}`}>
            <option value="">Add...</option>
            {options.filter((o) => !values.includes(o.id)).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
        ) : (
          <input className="input h-7 text-xs w-44" value={text} placeholder="Type and press Enter" aria-label={`Add to ${label}`}
                 onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } }} />
        )}
      </div>
    </Field>
  );
}

function Section({ title, subtitle, children, id }: { title: string; subtitle: string; children: ReactNode; id: string }) {
  return (
    <section className="card p-5 grid gap-4 scroll-mt-40 min-w-0" id={id} aria-label={title}>
      <div><h2 className="text-lg font-semibold">{title}</h2><p className="text-sm text-ink-muted">{subtitle}</p></div>
      {children}
    </section>
  );
}

function ItemsEditor({ label, hint, items, onChange }: { label: string; hint?: string; items: Item[]; onChange: (v: Item[]) => void }) {
  return (
    <div className="grid gap-2 min-w-0">
      <span className="grid"><span className="text-sm font-medium">{label}</span>{hint && <span className="text-xs text-ink-muted">{hint}</span>}</span>
      {items.map((it, i) => (
        <div key={i} className="flex gap-2 min-w-0">
          <input className="input h-8 w-32 sm:w-60 shrink-0 font-mono text-xs" value={it.id} aria-label={`${label} id`}
                 onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, id: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") } : x)))} />
          <input className="input h-8 flex-1 min-w-0" value={it.name} aria-label={`${label} name`}
                 onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <button type="button" className="btn-ghost h-8 px-2" aria-label="Remove" onClick={() => onChange(items.filter((_, j) => j !== i))}><Trash2 className="w-4 h-4" /></button>
        </div>
      ))}
      <button type="button" className="btn-secondary h-8 w-fit" onClick={() => onChange([...items, { id: `new_${items.length + 1}`, name: "New item" }])}>
        <Plus className="w-4 h-4" aria-hidden />Add
      </button>
    </div>
  );
}

// --- diff ---------------------------------------------------------------------------------------------------------
function flat(o: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  if (Array.isArray(o) && o.every((x) => x && typeof x === "object" && "id" in (x as object))) {
    for (const x of o as { id: string }[]) Object.assign(out, flat(x, `${prefix}[${x.id}]`));
  } else if (o && typeof o === "object" && !Array.isArray(o)) {
    for (const [k, v] of Object.entries(o)) Object.assign(out, flat(v, prefix ? `${prefix}.${k}` : k));
  } else out[prefix] = JSON.stringify(o);
  return out;
}
function changes(a: Policy, b: Policy): { path: string; before: string; after: string }[] {
  const fa = flat(a), fb = flat(b);
  return [...new Set([...Object.keys(fa), ...Object.keys(fb)])].filter((k) => fa[k] !== fb[k])
    .map((k) => ({ path: k, before: fa[k] ?? "(none)", after: fb[k] ?? "(removed)" }));
}

// --- editor -------------------------------------------------------------------------------------------------------
export function PolicyEditor({ active, activeVersion, defaults, versions }: {
  active: Policy; activeVersion: number | null; defaults: Policy; versions: PolicyVersionRow[];
}) {
  const router = useRouter();
  const [draft, setDraft] = useState<Policy>(() => structuredClone(active));
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [note, setNote] = useState("");
  const diff = useMemo(() => changes(active, draft), [active, draft]);
  const update = (fn: (p: Policy) => void) => setDraft((cur) => { const next = structuredClone(cur); fn(next); return next; });
  const caps = draft.lists.wanted_capabilities;
  const units = draft.lists.business_units;
  const trSum = Object.values(draft.weights.track_record).reduce((s, v) => s + v, 0);

  async function call(path: string, body: unknown, label: string) {
    setBusy(label); setMessage(null);
    try {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) {
        const details = Array.isArray(j.data?.details) ? `: ${j.data.details.map((d: { loc: string[]; msg: string }) => `${d.loc.join(".")} ${d.msg}`).join("; ")}` : "";
        throw new Error(`${j.detail ?? `Request failed (${r.status})`}${details}`);
      }
      return j;
    } catch (e) {
      setMessage({ tone: "error", text: e instanceof Error ? e.message : "Something went wrong." });
      return null;
    } finally { setBusy(null); }
  }

  const runPreview = async () => { const j = await call("/api/policy/preview", { policy: draft }, "preview"); if (j) setPreview(j as Preview); };
  const save = async (rescore: boolean) => {
    const j = await call("/api/policy/save", { policy: draft, note, rescore }, rescore ? "rescore" : "save");
    if (j) { setMessage({ tone: "ok", text: `Saved as version ${j.version} and made active.${j.rescore ? ` ${j.rescore}` : " Re-score to apply it to the shortlist."}` }); setNote(""); router.refresh(); }
  };
  const activate = async (version: number) => {
    const j = await call("/api/policy/activate", { version, rescore: false }, `activate-${version}`);
    if (j) { setMessage({ tone: "ok", text: `Version ${version} is active again. Re-score to apply it.` }); router.refresh(); }
  };

  return (
    <div className="grid gap-6">
      {/* Action bar */}
      <div className="sticky top-[65px] z-20 -mx-1 px-1 -mt-3 pt-3 pb-1 bg-surface-alt">
      <div className="card p-4 flex flex-wrap items-center gap-x-4 gap-y-3 shadow-pop" data-testid="policy-bar">
        <div className="text-sm">
          <span className="font-semibold">{activeVersion ? `Active: version ${activeVersion}` : "Active: defaults from config"}</span>
          <span className={`chip ml-2 ${diff.length ? "bg-warning-subtle text-warning" : "bg-surface-alt text-ink-muted"}`} data-testid="policy-changes">
            {diff.length ? `${diff.length} unsaved change${diff.length === 1 ? "" : "s"}` : "No changes"}
          </span>
        </div>
        <nav className="order-last w-full hidden md:flex gap-1 text-sm text-ink-muted border-t border-border pt-2 -mb-1" aria-label="Policy sections">
          {[["categories", "Categories"], ["filters", "Filters"], ["weights", "Weights"], ["selection", "Selection"], ["lists", "Lists"], ["history", "History"]].map(([id, l]) => (
            <a key={id} href={`#${id}`} className="px-2 py-1 rounded-control hover:bg-surface-alt hover:text-brand-strong">{l}</a>))}
        </nav>
        <div className="ml-auto flex flex-wrap gap-2">
          <button type="button" className="btn-ghost h-9" disabled={!diff.length || !!busy} onClick={() => { setDraft(structuredClone(active)); setPreview(null); }} data-testid="policy-discard">
            <Undo2 className="w-4 h-4" aria-hidden />Discard
          </button>
          <button type="button" className="btn-ghost h-9" disabled={!!busy} onClick={() => setDraft(structuredClone(defaults))} data-testid="policy-defaults" title="Load the defaults from config/*.yaml into the form">
            <RotateCcw className="w-4 h-4" aria-hidden />Defaults
          </button>
          <button type="button" className="btn-secondary h-9" disabled={!!busy} onClick={runPreview} data-testid="policy-preview" title="Show how the shortlist would change (no AI calls)">
            {busy === "preview" ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Eye className="w-4 h-4" aria-hidden />}Preview
          </button>
          <button type="button" className="btn-secondary h-9" disabled={!diff.length || !!busy} onClick={() => save(false)} data-testid="policy-save" title="Save as a new version and make it active">
            {busy === "save" ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Save className="w-4 h-4" aria-hidden />}Save
          </button>
          <button type="button" className="btn-primary h-9" disabled={!diff.length || !!busy} onClick={() => save(true)} data-testid="policy-save-rescore" title="Save, make active and re-score the shortlist now">
            {busy === "rescore" ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Save className="w-4 h-4" aria-hidden />}Save & re-score
          </button>
        </div>
        {diff.length > 0 && (
          <input className="input h-9 w-full" placeholder="Note for this version (what changed and why)" value={note}
                 onChange={(e) => setNote(e.target.value)} aria-label="Version note" data-testid="policy-note" />
        )}
        {busy === "preview" && <p className="w-full text-sm text-ink-muted" role="status">Recomputing every score under both policies from stored data (no AI calls), about 20 to 60 seconds...</p>}
        {message && <p role="status" className={`w-full text-sm ${message.tone === "error" ? "text-danger" : "text-success"}`} data-testid="policy-message">{message.text}</p>}
      </div>
      </div>

      {diff.length > 0 && (
        <section className="card p-4 grid gap-2" aria-label="Unsaved changes" data-testid="policy-diff">
          <h2 className="text-sm font-semibold">Unsaved changes</h2>
          <ul className="grid gap-1 text-xs font-mono max-h-48 overflow-y-auto">
            {diff.slice(0, 60).map((d) => <li key={d.path}><span className="text-ink-muted">{d.path}</span>: {d.before} <span className="text-brand-strong">to</span> {d.after}</li>)}
          </ul>
        </section>
      )}

      {preview && <PreviewPanel p={preview} />}

      <Section id="categories" title="Fit categories" subtitle="How a company can fit upGrad. Each category scores capability match, business-unit match and both, times location, size and stage factors. The best category becomes the company's fit type.">
        <div className="grid lg:grid-cols-2 gap-4">
          {draft.categories.map((c, i) => {
            const set = (fn: (c: Category) => void) => update((p) => fn(p.categories[i]));
            const isNew = !active.categories.some((a) => a.id === c.id) || !defaults.categories.some((d) => d.id === c.id) && !active.categories.some((a) => a.id === c.id);
            return (
              <div key={i} className={`rounded-card border p-4 grid gap-4 min-w-0 ${c.enabled ? "border-border" : "border-dashed border-border-strong opacity-70"}`} data-testid="policy-category">
                <div className="flex flex-wrap items-center gap-2">
                  <FitBadge fit={defaults.categories.some((d) => d.id === c.id) ? c.id : undefined} />
                  <input className="input h-9 flex-1 min-w-0 basis-40 font-semibold" value={c.label} aria-label="Category name" data-testid="category-label"
                         onChange={(e) => set((x) => { x.label = e.target.value; })} />
                  <Toggle label="Enabled" hint="Off = this category is never picked as a company's fit type." checked={c.enabled} onChange={(v) => set((x) => { x.enabled = v; })} />
                  <button type="button" className="btn-ghost h-8 px-2" aria-label={`Delete ${c.label}`} disabled={draft.categories.length <= 1}
                          onClick={() => update((p) => { p.categories.splice(i, 1); })}><Trash2 className="w-4 h-4" /></button>
                </div>
                <Field label="Id" hint="Internal name saved with every score so past runs stay readable. Lowercase letters, digits and _ only.">
                  <input className="input h-8 font-mono text-xs" value={c.id} readOnly={!isNew} aria-label="Category id"
                         onChange={(e) => set((x) => { x.id = e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 20); })} />
                </Field>
                <Field label="Definition" hint="What this category means, for people reading the shortlist. Text only: the settings below decide the score."><textarea className="input min-h-16 py-2" value={c.description} aria-label="Category definition"
                                                    onChange={(e) => set((x) => { x.description = e.target.value; })} /></Field>
                <div className="grid sm:grid-cols-3 gap-x-4 gap-y-3">
                  <Slider label="Capability match" hint="Credit for offering capabilities upGrad wants. 2 matches = full credit." value={c.rule.capability_weight} onChange={(v) => set((x) => { x.rule.capability_weight = v; })} />
                  <Slider label="Business-unit match" hint="Credit for serving an upGrad business unit. 2 matches = full credit." value={c.rule.segment_weight} onChange={(v) => set((x) => { x.rule.segment_weight = v; })} />
                  <Slider label="Both together" hint="Extra credit for matching a unit and a capability at once." value={c.rule.both_weight} onChange={(v) => set((x) => { x.rule.both_weight = v; })} />
                </div>
                <Tags label="Must have one of these capabilities" hint="If set, a company scores 0 here unless it has at least one of these verified capabilities. Empty = no requirement." values={c.rule.require_capabilities} options={caps}
                      onChange={(v) => set((x) => { x.rule.require_capabilities = v; })} />
                <Tags label="Must be in one of these business units" hint="If set, a company scores 0 here unless it serves at least one of these business units. Empty = no requirement." values={c.rule.require_segments} options={units}
                      onChange={(v) => set((x) => { x.rule.require_segments = v; })} />
                <div className="grid sm:grid-cols-2 gap-3">
                  <NumberBox label="Min raised" hint="Companies with less verified funding score 0 in this category. Empty = no minimum." unit="USD million" scale={1e6} value={c.rule.min_raised_usd} onChange={(v) => set((x) => { x.rule.min_raised_usd = v; })} />
                  <NumberBox label="Max raised" hint="Companies with more verified funding score 0 in this category (e.g. too big to acquire). Empty = no maximum." unit="USD million" scale={1e6} value={c.rule.max_raised_usd} onChange={(v) => set((x) => { x.rule.max_raised_usd = v; })} />
                </div>
                <Slider label="When size is unverified" hint="Multiplier when funding and headcount are not verified. 1 = no penalty; 0.7 = a 30% lower score until size is verified." value={c.rule.unknown_size_factor}
                        onChange={(v) => set((x) => { x.rule.unknown_size_factor = v; })} />
                <div className="grid sm:grid-cols-3 gap-x-4 gap-y-3">
                  <Slider label="Very early" hint="Multiplier for seed-only, under $3M raised. 1 = no change." value={c.rule.stage.very_early} onChange={(v) => set((x) => { x.rule.stage.very_early = v; })} />
                  <Slider label="Early" hint="Multiplier for under $10M, up to pre-Series A. 1 = no change." value={c.rule.stage.early} onChange={(v) => set((x) => { x.rule.stage.early = v; })} />
                  <Slider label="Later" hint="Multiplier past early stage. Lower = favours young companies." value={c.rule.stage.later} onChange={(v) => set((x) => { x.rule.stage.later = v; })} />
                </div>
                <div className="grid sm:grid-cols-2 gap-3">
                  <Slider label="Founder boost" hint="Added to this score (max 1) when a founder's T clears the bar. 0 = no boost." value={c.rule.founder_boost}
                          onChange={(v) => set((x) => { x.rule.founder_boost = v; if (x.rule.founder_boost_min_t === null) x.rule.founder_boost_min_t = 0.4; })} />
                  <Slider label="Strong founder: T at least" hint="Founder track record (T, 0 to 1) needed for the boost. 0.4 = a solid record." value={c.rule.founder_boost_min_t ?? 0.4}
                          onChange={(v) => set((x) => { x.rule.founder_boost_min_t = v; })} />
                </div>
                <div className="flex flex-wrap gap-6">
                  <Toggle label="Boost only early companies" hint="On = the founder boost applies only to very early and early companies." checked={c.rule.founder_boost_early_only} onChange={(v) => set((x) => { x.rule.founder_boost_early_only = v; })} />
                  <Toggle label="Apply the India-first location weight" hint="On = score x location: India 1, unverified 0.6, outside 0.3 (config defaults). Off = location ignored here." checked={c.rule.use_geo} onChange={(v) => set((x) => { x.rule.use_geo = v; })} />
                </div>
              </div>
            );
          })}
        </div>
        <button type="button" className="btn-secondary w-fit" data-testid="policy-add-category" onClick={() => update((p) => {
          const n = p.categories.length + 1;
          p.categories.push({ id: `custom_${n}`, label: `New category ${n}`, description: "", enabled: true, rule: structuredClone(p.categories[0]?.rule ?? defaults.categories[0].rule) });
        })}><Plus className="w-4 h-4" aria-hidden />Add a category</button>
      </Section>

      <Section id="filters" title="Filters" subtitle="Who can be scored and shortlisted at all. Hard exclusions remove a company; shortlist checks keep it scored but off the shortlist.">
        <div className="grid lg:grid-cols-3 gap-6">
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">Hard exclusions</h3>
            <Toggle label="Education businesses only" hint="Needs a verified education segment, capability or description. Off = non-education companies can be scored too." checked={draft.filters.require_education} onChange={(v) => update((p) => { p.filters.require_education = v; })} testId="filter-education" />
            <Toggle label="Exclude inactive companies" hint="Removes companies whose website is dead and that show no activity in 12 months. Unclear cases still pass." checked={draft.filters.exclude_inactive} onChange={(v) => update((p) => { p.filters.exclude_inactive = v; })} />
            <Toggle label="Exclude acquired companies" hint="Removes companies with any verified 'acquired' fact: they are no longer independent to partner with or buy." checked={draft.filters.exclude_acquired} onChange={(v) => update((p) => { p.filters.exclude_acquired = v; })} />
            <Toggle label="Exclude scaled competitors" hint="Removes companies on the Scaled competitors list (Lists tab), the large players upGrad competes with." checked={draft.filters.exclude_competitors} onChange={(v) => update((p) => { p.filters.exclude_competitors = v; })} />
            <Toggle label="Exclude upGrad's own brands" hint="Removes upGrad and the companies it already owns (Lists tab), so they never show up as targets." checked={draft.filters.exclude_upgrad_owned} onChange={(v) => update((p) => { p.filters.exclude_upgrad_owned = v; })} />
          </div>
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">Size and stage</h3>
            <NumberBox label="Max raised" hint="Companies with more verified funding are left out as too large. Empty = no ceiling." unit="USD million" scale={1e6} value={draft.filters.max_total_raised_usd} onChange={(v) => update((p) => { p.filters.max_total_raised_usd = v; })} testId="filter-max-raised" />
            <NumberBox label="Max headcount" hint="Companies with more verified employees are left out as too large. Empty = no ceiling." value={draft.filters.max_headcount} onChange={(v) => update((p) => { p.filters.max_headcount = v; })} />
            <NumberBox label="Min raised" hint="Leaves out companies with less verified funding. No verified funding counts as $0, so those are left out too." unit="USD million" scale={1e6} value={draft.filters.min_total_raised_usd} onChange={(v) => update((p) => { p.filters.min_total_raised_usd = v; })} />
            <Toggle label="Require verified funding" hint="On = companies without a verified funding amount are left out. Off = bootstrapped or unverified ones can still make it." checked={draft.filters.require_verified_funding} onChange={(v) => update((p) => { p.filters.require_verified_funding = v; })} />
            <Tags label="Exclude funding stages" hint="Leaves out companies with a verified round at any of these stages, e.g. series c, series d for later-stage ones." values={draft.filters.excluded_stages} onChange={(v) => update((p) => { p.filters.excluded_stages = v.map((s) => s.toLowerCase()); })} />
            <div className="grid grid-cols-2 gap-3">
              <NumberBox label="Founded in or after" hint="Leaves out companies founded before this year. Unknown founding year is kept." value={draft.filters.founded_after} onChange={(v) => update((p) => { p.filters.founded_after = v; })} />
              <NumberBox label="Founded in or before" hint="Leaves out companies founded after this year. Empty = no limit." value={draft.filters.founded_before} onChange={(v) => update((p) => { p.filters.founded_before = v; })} />
            </div>
          </div>
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">Location, evidence and score</h3>
            <div className="grid gap-2">
              <span className="grid"><span className="text-sm font-medium">Regions allowed</span>
                <span className="text-xs text-ink-muted">Off = companies in that location are left out entirely. To prefer India without dropping others, use the region multipliers under Weights.</span></span>
              {(["india", "outside", "unknown"] as const).map((r) => (
                <Toggle key={r} label={{ india: "India", outside: "Outside India", unknown: "Location unverified" }[r]} testId={`region-allowed-${r}`}
                        checked={draft.filters.regions_allowed[r] ?? true} onChange={(v) => update((p) => { p.filters.regions_allowed[r] = v; })} />
              ))}
            </div>
            <Toggle label="Founder identity verified" hint="Shortlist check: a founder is confirmed by a non-social source (LinkedIn or X alone is not enough). Failing companies stay scored but off the shortlist." checked={draft.filters.require_identity_verified} onChange={(v) => update((p) => { p.filters.require_identity_verified = v; })} />
            <Toggle label="Founder's current role verified" hint="Shortlist check: a verified non-LinkedIn source shows the founder still works at the company." checked={draft.filters.require_current_role} onChange={(v) => update((p) => { p.filters.require_current_role = v; })} />
            <Toggle label="Company existence verified" hint="Shortlist check: the company's own website or a company registry confirms it exists." checked={draft.filters.require_company_existence} onChange={(v) => update((p) => { p.filters.require_company_existence = v; })} />
            <NumberBox label="Min verified track-record facts" hint="Shortlist check: verified facts needed about founder track record or company traction. Higher = stricter evidence bar." allowEmpty={false} value={draft.filters.min_track_record_claims} onChange={(v) => update((p) => { p.filters.min_track_record_claims = v ?? 0; })} />
            <NumberBox label="Min verified claims" hint="Leaves out companies with fewer verified facts in total than this. 0 = off." allowEmpty={false} value={draft.filters.min_verified_claims} onChange={(v) => update((p) => { p.filters.min_verified_claims = v ?? 0; })} />
            <Slider label="Min confidence" hint="Confidence = share of expected facts that are verified (10 per company + 4 per founder). Below this a company is left out; 0 = off." value={draft.filters.min_confidence} onChange={(v) => update((p) => { p.filters.min_confidence = v; })} />
            <Slider label="Min final score" hint="Leaves out companies whose final score (after the AI judge) is below this, even if the list ends up shorter. 0 = off." step={0.01} value={draft.filters.min_final_score} onChange={(v) => update((p) => { p.filters.min_final_score = v; })} />
          </div>
        </div>
      </Section>

      <Section id="weights" title="Scoring weights" subtitle="Final = T^a x I^b x F^c x C^d x region multiplier, then blended with the pairwise judge. All numbers are used by code; the AI never scores.">
        <div className="grid lg:grid-cols-3 gap-6">
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">Track record (T), per founder</h3>
            {Object.keys(draft.weights.track_record).map((k) => (
              <Slider key={k} label={TR_LABELS[k] ?? k} hint={`${TR_HINTS[k] ?? ""} Higher = it counts more toward T.`.trim()} value={draft.weights.track_record[k]} onChange={(v) => update((p) => { p.weights.track_record[k] = v; })} />
            ))}
            <p className={`text-xs ${Math.abs(trSum - 1) > 0.001 ? "text-warning" : "text-ink-muted"}`}>Sum {trSum.toFixed(2)} (1.00 keeps T on a 0 to 1 scale; T is capped at 1)</p>
            <Slider label="Team-depth bonus" hint="Added to T when at least two co-founders each have a verified track-record fact. Rewards strong teams over one star." value={draft.weights.team_depth_bonus} onChange={(v) => update((p) => { p.weights.team_depth_bonus = v; })} />
          </div>
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">Interesting (I) and Final</h3>
            <Slider label="Momentum" hint="Share of I from momentum: recent funding, hiring, news and GitHub traction. Momentum + novelty normally sum to 1." value={draft.weights.momentum} onChange={(v) => update((p) => { p.weights.momentum = v; })} />
            <Slider label="Novelty" hint="Share of I from novelty: how far the idea is from crowded clusters, times how fast its theme is growing." value={draft.weights.novelty} onChange={(v) => update((p) => { p.weights.novelty = v; })} />
            <NumberBox label="Momentum half-life" hint="How fast old signals fade. At 180, a 6-month-old funding round or news item counts half as much as a fresh one." unit="days" allowEmpty={false} value={draft.weights.momentum_half_life_days} onChange={(v) => update((p) => { p.weights.momentum_half_life_days = v ?? 180; })} />
            {(["T", "I", "F", "C"] as const).map((k) => (
              <Slider key={k} label={`Exponent ${k}`} hint={EXPONENT_HINTS[k]} max={2} value={draft.weights.final_exponents[k]} onChange={(v) => update((p) => { p.weights.final_exponents[k] = v; })} />
            ))}
          </div>
          <div className="grid gap-3 content-start">
            <h3 className="text-sm font-semibold text-ink-muted uppercase tracking-wide">India first, judge, shrinkage</h3>
            {(["india", "unknown", "outside"] as const).map((k) => (
              <Slider key={k} label={`Region multiplier: ${{ india: "India", unknown: "unverified", outside: "outside India" }[k]}`} hint={REGION_HINTS[k]} value={draft.weights.region_multiplier[k] ?? 1}
                      onChange={(v) => update((p) => { p.weights.region_multiplier[k] = v; })} testId={`weight-region-${k}`} />
            ))}
            <Slider label="Pairwise judge share" hint="How much the AI judge's head-to-head ranking counts. 0.3 = 30% judge, 70% the code score; 0 = code score only." value={draft.weights.judge_blend} onChange={(v) => update((p) => { p.weights.judge_blend = v; })} />
            <Slider label="Small-evidence shrinkage (k)" hint="Pulls scores built on few verified facts toward the average, so one lucky fact can't top the list. Higher = stronger pull; 0 = off." min={0} max={10} step={0.5} value={draft.weights.shrinkage_k} onChange={(v) => update((p) => { p.weights.shrinkage_k = v; })} />
          </div>
        </div>
      </Section>

      <Section id="selection" title="Shortlist selection" subtitle="How the final list is picked from eligible companies: by score, with variety and minimums.">
        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-4">
          <NumberBox label="Shortlist size" hint="How many companies make the final shortlist in the Sheet and dashboard." allowEmpty={false} value={draft.selection.shortlist_size} onChange={(v) => update((p) => { p.selection.shortlist_size = v ?? 15; })} testId="selection-size" />
          <NumberBox label="Min per category" hint="At least this many picks per fit category (e.g. Acquire, Partner, Co-build) when enough qualify." allowEmpty={false} value={draft.selection.min_per_category} onChange={(v) => update((p) => { p.selection.min_per_category = v ?? 0; })} />
          <NumberBox label="Min India-located" hint="At least this many India-located companies on the shortlist when enough qualify." allowEmpty={false} value={draft.selection.min_india} onChange={(v) => update((p) => { p.selection.min_india = v ?? 0; })} />
          <Slider label="Score vs variety" hint="1 = pick strictly by score. Lower = skip near-duplicates (same idea, same fit type) for a more varied list." value={draft.selection.diversity_lambda} onChange={(v) => update((p) => { p.selection.diversity_lambda = v; })} />
          <NumberBox label="Judge compares top" hint="How many top-scoring companies the AI judge compares head to head. More = broader judging, more Gemini cost." allowEmpty={false} value={draft.selection.judge_top_n} onChange={(v) => update((p) => { p.selection.judge_top_n = v ?? 60; })} />
        </div>
      </Section>

      <Section id="lists" title="Lists" subtitle="Names and vocabularies the rules use. Business units and capabilities define what 'fit' means.">
        <div className="grid lg:grid-cols-2 xl:grid-cols-4 gap-6">
          <Tags label="Scaled competitors" hint="Large players upGrad competes with. Removed when 'Exclude scaled competitors' is on under Filters." values={draft.lists.competitors} onChange={(v) => update((p) => { p.lists.competitors = v; })} testId="list-competitors" />
          <Tags label="upGrad's own brands" hint="upGrad and companies it owns. Removed when 'Exclude upGrad's own brands' is on under Filters." values={draft.lists.upgrad_owned} onChange={(v) => update((p) => { p.lists.upgrad_owned = v; })} />
          <Tags label="Blocklist (never shortlisted)" hint="Companies that never reach the shortlist, whatever their score. Use for ones already reviewed and rejected." values={draft.lists.blocklist} onChange={(v) => update((p) => { p.lists.blocklist = v; })} testId="list-blocklist" />
          <Tags label="Must consider (reviewer picks)" hint="Guaranteed a shortlist slot when every gate and verification check passes. Picks that fail stay in Left out with the reason." values={draft.lists.must_consider ?? []} onChange={(v) => update((p) => { p.lists.must_consider = v; })} testId="list-must-consider" />
        </div>
        <div className="grid lg:grid-cols-2 gap-6">
          <ItemsEditor label="upGrad business units" hint="upGrad's business areas. Companies serving these earn business-unit match; the AI also tags new pages against this list." items={draft.lists.business_units} onChange={(v) => update((p) => { p.lists.business_units = v; })} />
          <ItemsEditor label="Wanted capabilities" hint="Capabilities upGrad wants to add. Companies offering these earn capability match; the left box is the stored id, the right the readable name." items={draft.lists.wanted_capabilities} onChange={(v) => update((p) => { p.lists.wanted_capabilities = v; })} />
        </div>
      </Section>

      <Section id="history" title="Version history" subtitle="Every saved policy, newest first. Activating an earlier version is a one-click rollback; re-score to apply it.">
        {versions.length === 0 ? <p className="text-sm text-ink-muted">No saved versions yet: the defaults from config are active.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px]" data-testid="policy-history">
              <thead className="bg-surface-alt"><tr>{["Version", "Status", "Saved", "By", "Note", "Changes", "Runs", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version} className="border-t border-border">
                    <td className="td tabular font-medium">v{v.version}</td>
                    <td className="td"><span className={`chip ${v.status === "active" ? "bg-success-subtle text-success" : "bg-surface-alt text-ink-muted"}`}>{v.status}</span></td>
                    <td className="td whitespace-nowrap">{day(v.created_at)} {time(v.created_at)}</td>
                    <td className="td">{v.author}</td>
                    <td className="td text-ink-muted max-w-xs">{v.note}</td>
                    <td className="td tabular">{v.changes.length}</td>
                    <td className="td tabular">{v.runs_scored}</td>
                    <td className="td">
                      {v.status !== "active" && (
                        <button type="button" className="btn-secondary h-8" disabled={!!busy} onClick={() => activate(v.version)} data-testid="policy-activate">
                          {busy === `activate-${v.version}` ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <History className="w-4 h-4" aria-hidden />}Activate
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function PreviewPanel({ p }: { p: Preview }) {
  const list = (rows: Row[], icon: ReactNode, empty: string, testId: string) => rows.length === 0 ? <p className="text-sm text-ink-muted">{empty}</p> : (
    <ul className="grid gap-1.5" data-testid={testId}>
      {rows.map((r) => (
        <li key={r.entity_id} className="flex flex-wrap items-center gap-2 text-sm">
          {icon}<span className="tabular text-ink-muted">#{r.rank}</span>
          <Link className="font-medium hover:text-brand-strong" href={`/company/${r.entity_id}`}>{r.company}</Link>
          <FitBadge fit={r.fit_type} /><RegionBadge region={r.region} />
        </li>
      ))}
    </ul>
  );
  const reasons = [...new Set([...Object.keys(p.current.excluded_by_reason), ...Object.keys(p.draft.excluded_by_reason)])];
  return (
    <section className="card p-5 grid gap-4 border-brand/30 animate-slide-up" aria-label="Preview" data-testid="policy-preview-panel">
      <div>
        <h2 className="text-lg font-semibold">Impact preview</h2>
        <p className="text-sm text-ink-muted">{p.note} &ldquo;Current&rdquo; is a fresh re-score under the active policy, so it can differ from the published list when data changed since the last run.</p>
      </div>
      <div className="grid md:grid-cols-3 gap-4">
        <div><h3 className="text-sm font-semibold mb-2">Would enter the shortlist ({p.entered.length})</h3>{list(p.entered, <ArrowUpRight className="w-4 h-4 text-success" aria-hidden />, "Nobody new.", "preview-entered")}</div>
        <div><h3 className="text-sm font-semibold mb-2">Would leave ({p.left.length})</h3>{list(p.left, <ArrowDownRight className="w-4 h-4 text-danger" aria-hidden />, "Nobody leaves.", "preview-left")}</div>
        <div><h3 className="text-sm font-semibold mb-2">Rank changes ({p.moved.length})</h3>
          {p.moved.length === 0 ? <p className="text-sm text-ink-muted">No rank changes.</p> : (
            <ul className="grid gap-1 text-sm">{p.moved.map((r) => <li key={r.entity_id}><span className="font-medium">{r.company}</span> <span className="text-ink-muted tabular">#{r.from} to #{r.rank}</span></li>)}</ul>)}
        </div>
      </div>
      <div className="grid md:grid-cols-3 gap-4 text-sm">
        <div><h3 className="font-semibold mb-1">By category</h3>
          {Object.entries(p.draft.by_category).map(([k, v]) => <p key={k} className="flex items-center gap-2"><FitBadge fit={k} /> {v} <span className="text-ink-muted">(now {p.current.by_category[k] ?? 0})</span></p>)}</div>
        <div><h3 className="font-semibold mb-1">By region</h3>
          {Object.entries(p.draft.by_region).map(([k, v]) => <p key={k} className="flex items-center gap-2"><RegionBadge region={k} /> {v} <span className="text-ink-muted">(now {p.current.by_region[k] ?? 0})</span></p>)}</div>
        <div><h3 className="font-semibold mb-1">Excluded, by reason</h3>
          {reasons.map((k) => <p key={k}>{k}: <span className="tabular">{p.draft.excluded_by_reason[k] ?? 0}</span> <span className="text-ink-muted">(now {p.current.excluded_by_reason[k] ?? 0})</span></p>)}
          <p className="text-ink-muted mt-1">Eligible for the shortlist: {p.draft.eligible} (now {p.current.eligible}) of {p.draft.scored} scored</p></div>
      </div>
      {p.draft.policy_filtered.length > 0 && (
        <details className="text-sm"><summary className="cursor-pointer font-medium">Removed by the new filters ({p.draft.policy_filtered.length})</summary>
          <ul className="mt-2 grid gap-1">{p.draft.policy_filtered.map((x) => <li key={x.company}><span className="font-medium">{x.company}</span>: <span className="text-ink-muted">{x.why}</span></li>)}</ul>
        </details>
      )}
    </section>
  );
}
