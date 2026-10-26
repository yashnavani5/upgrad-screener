"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Building2, Database, ExternalLink, Globe2, Landmark, Loader2, Mic, MicOff, Search, Sparkles, User, Volume2, VolumeX, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { clean, day, domain, usd } from "@/lib/format";
import type { SearchHit } from "@/lib/schemas";

import { FitBadge, RegionBadge } from "./ui";

type Fact = { field: string; value: Record<string, unknown>; source_url: string };
type Verified = {
  entity_id: string; company: string; website: string | null; description: string | null; location: string | null;
  region: string; status: string; founded_year: number | null; total_raised_usd: number | null; headcount: number | null;
  fit_type: string | null; shortlist_rank: number | null; claims_count: number;
  rounds: { round_type?: string | null; amount_usd?: number | null; announced_on?: string | null; investors: string[]; source_url: string }[];
  founders: { name: string; title?: string | null; line?: string | null; linkedin_url?: string | null; x_url?: string | null;
    identity_verified: boolean; background: Fact[] }[];
  financial_claims: { field: string; value: unknown; source_url: string; evidence_span: string }[];
};
type Insight = {
  rank: number; company: string; entity_id: string; fit_type: string | null; region: string; line: string; detail?: string;
  founders: { name: string; title?: string | null; line?: string | null }[];
};
type Answer = {
  q: string; matches: SearchHit[]; insights?: { title: string; items: Insight[]; note: string }; target: SearchHit | null; verified: Verified | null; registry: SearchHit | null;
  web: { text: string; sources: { n: number; title: string; url: string }[]; queries: string[]; ms: number;
         cached?: boolean; cached_at?: string; cached_age_hours?: number } | null;
  web_error: string | null;
};

// Minimal Web Speech API typings (Chrome / Edge ship it as webkitSpeechRecognition).
type Recognition = {
  lang: string; interimResults: boolean; continuous: boolean; start(): void; stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null; onerror: ((e: { error: string }) => void) | null;
};
function hasVoice(): boolean {
  return typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);
}
function recognizer(): Recognition | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  const C = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  return C ? new C() : null;
}

const KIND_ICON = { company: Building2, founder: User, registry: Landmark } as const;
const KIND_LABEL = { company: "Company", founder: "Founder", registry: "MCA registry" } as const;
const inr = (v: number | null | undefined) => (v == null ? "-" : `INR ${Math.round(v).toLocaleString("en-IN")}`);

function factText(f: Fact): string {
  const v = f.value as Record<string, string | number | null | undefined>;
  if (f.field === "education") return `Studied at ${v.institution}${v.degree ? ` (${v.degree})` : ""}`;
  if (f.field === "prior_exit") return `Exit: ${v.company}${v.acquirer ? `, acquired by ${v.acquirer}` : ""}`;
  const years = [v.start_year, v.end_year].filter(Boolean).join("-");
  return `${v.role ? `${v.role}, ` : ""}${v.company}${years ? ` (${years})` : ""}`;
}

/** Render the web answer: headings, bullets and [n] citations as links to the numbered sources. */
function WebText({ text, sources }: { text: string; sources: { n: number; url: string; title: string }[] }) {
  const byN = new Map(sources.map((s) => [s.n, s]));
  return (
    <div className="grid gap-1.5 text-sm" data-testid="ask-web-text">
      {text.split(/\n+/).filter((l) => l.trim()).map((line, i) => {
        const heading = !/^\s*[-*•]/.test(line) && line.trim().length < 60 && !/\[\d+\]/.test(line);
        const body = line.replace(/^\s*[-*•]\s*/, "");
        const parts = body.split(/(\[\d+\])/g);
        return (
          <p key={i} className={heading ? "font-semibold mt-2 text-ink" : "pl-3 border-l-2 border-border text-ink"}>
            {parts.map((p, j) => {
              const m = p.match(/^\[(\d+)\]$/);
              const src = m ? byN.get(Number(m[1])) : undefined;
              return src ? (
                <a key={j} href={src.url} target="_blank" rel="noopener noreferrer" title={src.title}
                   className="align-super text-[10px] font-semibold text-brand-strong hover:underline mx-0.5">{m![1]}</a>
              ) : <span key={j}>{clean(p)}</span>;
            })}
          </p>
        );
      })}
    </div>
  );
}

export function AskBox() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [active, setActive] = useState(0);
  const [loadingHits, setLoadingHits] = useState(false);
  const [asking, setAsking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [webLoading, setWebLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  // Voice input exists only in some browsers (Chrome, Edge); false on the server.
  const voiceOk = useSyncExternalStore(() => () => {}, hasVoice, () => false);
  const rec = useRef<Recognition | null>(null);
  const input = useRef<HTMLInputElement>(null);

  // Ctrl+K / Cmd+K and "/" open the box from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && /input|textarea|select/i.test(e.target.tagName);
      if ((e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        setOpen(true);
      }
    };
    const onOpen = (e: Event) => {  // other buttons can open the box: window.dispatchEvent(new CustomEvent("open-ask", {detail}))
      setOpen(true);
      const text = (e as CustomEvent<string | undefined>).detail;
      if (text) setQ(text);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-ask", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("open-ask", onOpen); };
  }, []);

  // Type-ahead over the database (debounced). Short input shows no list (derived below), so no reset is needed here.
  useEffect(() => {
    if (q.trim().length < 2) return;
    const ctl = new AbortController();
    const t = setTimeout(async () => {
      setLoadingHits(true);
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(q.trim())}`, { signal: ctl.signal });
        const j = (await r.json()) as { hits?: SearchHit[] };
        setHits(j.hits ?? []);
        setActive(0);
      } catch { /* aborted or offline: keep the last list */ } finally { setLoadingHits(false); }
    }, 180);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);

  const shown = q.trim().length < 2 ? [] : hits;

  useEffect(() => {
    if (!asking && !webLoading) return;
    const started = Date.now();
    const t = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => clearInterval(t);
  }, [asking, webLoading]);

  // Two phases: the database answer is instant; web research (Gemini + Google Search, ~20-30 s) is added when ready.
  const lastAsk = useRef<{ q: string; entityId?: string } | null>(null);
  const ask = useCallback(async (text: string, entityId?: string, refresh = false) => {
    const query = text.trim();
    if (query.length < 2) return;
    setAsking(true); setError(null); setAnswer(null); setElapsed(0);
    window.speechSynthesis?.cancel(); setSpeaking(false);
    lastAsk.current = { q: query, entityId };
    const post = async (web: boolean) => {
      const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: query, entity_id: entityId, web, refresh: web && refresh }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ?? `Request failed (${r.status})`);
      return j as Answer;
    };
    try {
      const first = await post(false);
      setAnswer(first);
      setAsking(false);
      if (first.insights) return;  // answered from the screener's own data: no web call needed
      setWebLoading(true);
      const full = await post(true);
      setAnswer((cur) => (cur && cur.q === full.q ? { ...cur, web: full.web, web_error: full.web_error } : full));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally { setAsking(false); setWebLoading(false); }
  }, []);

  const openHit = (h: SearchHit) => {
    if (h.company_id) { setOpen(false); router.push(`/company/${h.company_id}`); }
    else void ask(h.name, h.entity_id);
  };

  const toggleMic = () => {
    if (listening) { rec.current?.stop(); return; }
    const r = recognizer();
    if (!r) return;
    rec.current = r;
    r.lang = "en-IN"; r.interimResults = true; r.continuous = false;
    let finalText = "";
    r.onresult = (e) => {
      const parts = Array.from(e.results);
      const text = parts.map((p) => p[0].transcript).join(" ").trim();
      setQ(text);
      if (parts.every((p) => p.isFinal)) finalText = text;
    };
    r.onerror = (e) => { setError(e.error === "not-allowed" ? "Microphone access was blocked." : `Voice input stopped (${e.error}).`); };
    r.onend = () => { setListening(false); if (finalText) void ask(finalText); };
    setError(null); setListening(true); r.start();
  };

  const speakable = useMemo(() => {
    if (!answer) return "";
    const v = answer.verified;
    const verifiedPart = answer.insights
      ? `${answer.insights.title}. ${answer.insights.items.map((it) => `Number ${it.rank}, ${it.company}. ${clean(it.line)}`).join(" ")}`
      : v ? `${v.company}. ${clean(v.description)}. ${v.founders.map((f) => clean(f.line) || f.name).join(". ")}.` : "";
    const web = (answer.web?.text ?? "").replace(/\[\d+\]/g, "");
    return `${verifiedPart} ${web}`.slice(0, 2500);
  }, [answer]);

  const toggleSpeak = () => {
    const synth = window.speechSynthesis;
    if (!synth || !speakable) return;
    if (speaking) { synth.cancel(); setSpeaking(false); return; }
    const u = new SpeechSynthesisUtterance(speakable);
    u.lang = "en-IN"; u.rate = 1.02;
    u.onend = () => setSpeaking(false);
    setSpeaking(true); synth.speak(u);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, shown.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (e.altKey || !shown.length) void ask(q); else void ask(q, shown[active]?.company_id ?? shown[active]?.entity_id); }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { setOpen(o); if (!o) { rec.current?.stop(); window.speechSynthesis?.cancel(); setSpeaking(false); } }}>
      <Dialog.Trigger asChild>
        <button type="button" className="btn-secondary h-9 gap-2 px-3 text-ink-muted" data-testid="ask-open" aria-label="Search or ask (Ctrl+K)">
          <Search className="w-4 h-4" aria-hidden />
          <span className="hidden lg:inline">Search or ask</span>
          <kbd className="hidden lg:inline rounded border border-border px-1.5 text-[11px] font-mono text-ink-muted">Ctrl K</kbd>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_150ms_ease-out]" />
        <Dialog.Content
          className="fixed z-50 left-1/2 top-[6vh] -translate-x-1/2 w-[calc(100vw-24px)] max-w-3xl max-h-[88vh] card shadow-pop flex flex-col overflow-hidden data-[state=open]:animate-[pop-in_180ms_ease-out]"
          onOpenAutoFocus={(e) => { e.preventDefault(); input.current?.focus(); }} data-testid="ask-dialog">
          <Dialog.Title className="sr-only">Search or ask about a founder or company</Dialog.Title>
          <Dialog.Description className="sr-only">Type or speak a name. Results come from the screener database and from web research with sources.</Dialog.Description>
          <div className="flex items-center gap-2 border-b border-border p-3">
            <Search className="w-5 h-5 text-ink-muted shrink-0" aria-hidden />
            <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown} data-testid="ask-input"
                   placeholder="A name (Arivihan, Kirti Prakash Mishra) or a question (best fit founder, top 5 to acquire in India)"
                   className="flex-1 min-w-0 bg-transparent outline-none text-base placeholder:text-ink-muted" aria-label="Search or ask" />
            {loadingHits && <Loader2 className="w-4 h-4 animate-spin text-ink-muted" aria-hidden />}
            <button type="button" onClick={toggleMic} disabled={!voiceOk} data-testid="ask-mic"
                    title={voiceOk ? (listening ? "Stop listening" : "Speak a name") : "Voice input needs Chrome or Edge"}
                    className={`btn-ghost h-9 w-9 p-0 rounded-full ${listening ? "bg-brand text-white animate-pulse hover:bg-brand-hover" : ""}`}>
              {listening ? <MicOff className="w-4 h-4" aria-hidden /> : <Mic className="w-4 h-4" aria-hidden />}
              <span className="sr-only">{listening ? "Stop listening" : "Voice input"}</span>
            </button>
            <button type="button" className="btn-primary h-9" onClick={() => void ask(q)} disabled={asking || q.trim().length < 2} data-testid="ask-submit">
              {asking ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Sparkles className="w-4 h-4" aria-hidden />} Ask
            </button>
            <Dialog.Close className="btn-ghost h-9 w-9 p-0" aria-label="Close"><X className="w-4 h-4" aria-hidden /></Dialog.Close>
          </div>

          <div className="overflow-y-auto p-3 grid gap-3">
            {listening && <p className="text-sm text-brand-strong flex items-center gap-2" role="status"><Mic className="w-4 h-4 animate-pulse" aria-hidden />Listening... say a founder or company name.</p>}
            {!answer && !asking && shown.length > 0 && (
              <ul className="grid gap-1" role="listbox" aria-label="Matches" data-testid="ask-hits">
                {shown.map((h, i) => {
                  const Icon = KIND_ICON[h.kind];
                  return (
                    <li key={`${h.kind}-${h.entity_id}`} role="option" aria-selected={i === active}>
                      <button type="button" onMouseEnter={() => setActive(i)} onClick={() => openHit(h)} data-testid="ask-hit"
                              className={`w-full text-left rounded-control px-3 py-2 flex items-start gap-3 transition-colors ${i === active ? "bg-brand-subtle" : "hover:bg-surface-alt"}`}>
                        <Icon className="w-4 h-4 mt-1 shrink-0 text-ink-muted" aria-hidden />
                        <span className="min-w-0 flex-1">
                          <span className="font-medium">{h.name}</span>
                          {h.company && <span className="text-ink-muted">, {h.company}</span>}
                          <span className="block text-xs text-ink-muted truncate">
                            {KIND_LABEL[h.kind]}{h.location ? ` · ${h.location}` : ""}{h.description ? ` · ${clean(h.description)}` : ""}
                            {h.kind === "registry" && h.cin ? ` · CIN ${h.cin}` : ""}
                          </span>
                        </span>
                        {h.shortlist_rank ? <span className="chip bg-brand-subtle text-brand-hover shrink-0">Shortlist #{h.shortlist_rank}</span> : null}
                      </button>
                    </li>
                  );
                })}
                <li className="text-xs text-ink-muted px-3 pt-1">Enter asks about the highlighted match · Alt+Enter asks about exactly what you typed · click a company to open it</li>
              </ul>
            )}
            {!answer && !asking && q.trim().length >= 2 && !loadingHits && shown.length === 0 && (
              <p className="text-sm text-ink-muted px-1" data-testid="ask-nohits">Not in the screener database yet. Press Ask to research it on the web.</p>
            )}
            {!answer && !asking && q.trim().length < 2 && (
              <div className="text-sm text-ink-muted px-1 grid gap-1">
                <p>Search companies, founders and 1.4 lakh+ MCA-registered education companies, ask for a full profile with financials, or ask a question about the shortlist (&ldquo;best fit founder&rdquo;, &ldquo;top 5 to acquire in India&rdquo;, &ldquo;founders with an exit&rdquo;).</p>
                <p>Tip: press the mic and say a name. Answers can be read aloud.</p>
              </div>
            )}
            {asking && (
              <div className="grid gap-2" role="status" data-testid="ask-loading">
                <p className="text-sm text-ink-muted flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />
                  Checking the screener database...</p>
                <div className="h-24 rounded-card bg-surface-alt animate-pulse" /><div className="h-40 rounded-card bg-surface-alt animate-pulse" />
              </div>
            )}
            {error && <p className="text-sm text-danger" role="alert">{error}</p>}
            {answer && (
              <div className="grid gap-3" data-testid="ask-answer">
                <div className="flex items-center gap-2">
                  <button type="button" className="btn-ghost h-8 px-2 text-sm" onClick={() => setAnswer(null)}>Back to matches</button>
                  <button type="button" className="btn-secondary h-8 px-3 ml-auto" onClick={toggleSpeak} data-testid="ask-speak" disabled={!speakable}>
                    {speaking ? <VolumeX className="w-4 h-4" aria-hidden /> : <Volume2 className="w-4 h-4" aria-hidden />}{speaking ? "Stop" : "Read aloud"}
                  </button>
                </div>

                {answer.insights && (
                  <section className="rounded-card border border-success/30 bg-success-subtle/40 p-4 grid gap-3" data-testid="ask-insights">
                    <h3 className="text-sm font-semibold flex items-center gap-2 text-success"><Database className="w-4 h-4" aria-hidden />{answer.insights.title}</h3>
                    <p className="text-xs text-ink-muted">{answer.insights.note}</p>
                    {answer.insights.items.length === 0 ? <p className="text-sm">No shortlisted company matches that question yet.</p> : (
                      <ol className="grid gap-3">
                        {answer.insights.items.map((it, i) => (
                          <li key={`${it.entity_id}-${i}`} className="rounded-control bg-surface p-3 grid gap-1.5 border border-border" data-testid="ask-insight">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="w-7 h-7 rounded-full bg-brand-strong text-white text-sm font-semibold flex items-center justify-center tabular">{it.rank}</span>
                              <Link href={`/company/${it.entity_id}`} onClick={() => setOpen(false)} className="font-semibold hover:text-brand-strong">{it.company}</Link>
                              <FitBadge fit={it.fit_type} /><RegionBadge region={it.region} />
                            </div>
                            {it.detail && <p className="text-sm font-medium">{clean(it.detail)}</p>}
                            <p className="text-sm">{clean(it.line)}</p>
                            {it.founders.length > 0 && (
                              <ul className="text-sm text-ink-muted grid gap-0.5">
                                {it.founders.slice(0, 3).map((f) => (
                                  <li key={f.name}><span className="text-ink font-medium">{f.name}</span>{f.title ? `, ${clean(f.title)}` : ""}{f.line ? `: ${clean(f.line)}` : ""}</li>
                                ))}
                              </ul>
                            )}
                          </li>
                        ))}
                      </ol>
                    )}
                  </section>
                )}

                {answer.insights ? null : answer.verified ? (
                  <section className="rounded-card border border-success/30 bg-success-subtle/40 p-4 grid gap-2" data-testid="ask-verified">
                    <h3 className="text-sm font-semibold flex items-center gap-2 text-success"><Database className="w-4 h-4" aria-hidden />Verified by the screener ({answer.verified.claims_count} claims, every one with a source)</h3>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-lg font-semibold">{answer.verified.company}</span>
                      <FitBadge fit={answer.verified.fit_type} /><RegionBadge region={answer.verified.region} />
                      {answer.verified.shortlist_rank ? <span className="chip bg-brand-subtle text-brand-hover">Shortlist #{answer.verified.shortlist_rank}</span> : null}
                    </div>
                    {answer.verified.description && <p className="text-sm">{clean(answer.verified.description)}</p>}
                    <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-sm">
                      {[["Location", answer.verified.location ?? "-"], ["Founded", answer.verified.founded_year ?? "-"],
                        ["Raised (verified)", usd(answer.verified.total_raised_usd)], ["Headcount", answer.verified.headcount ?? "-"]].map(([k, v]) => (
                        <div key={k as string}><dt className="text-ink-muted text-xs">{k}</dt><dd className="font-medium">{v}</dd></div>))}
                    </dl>
                    {answer.verified.founders.length > 0 && (
                      <div className="grid gap-1.5 text-sm">
                        {answer.verified.founders.map((f) => (
                          <div key={f.name}>
                            <span className="font-medium">{f.name}</span>{f.title && <span className="text-ink-muted"> · {clean(f.title)}</span>}
                            {f.linkedin_url && <a className="link ml-2 text-xs" href={f.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn</a>}
                            {f.x_url && <a className="link ml-2 text-xs" href={f.x_url} target="_blank" rel="noopener noreferrer">X</a>}
                            {f.line && <p className="text-ink-muted">{clean(f.line)}</p>}
                            {!f.line && f.background.length > 0 && <p className="text-ink-muted">{f.background.slice(0, 3).map(factText).join(" · ")}</p>}
                          </div>))}
                      </div>
                    )}
                    {answer.verified.rounds.length > 0 && (
                      <ul className="text-sm grid gap-0.5">
                        {answer.verified.rounds.map((r, i) => (
                          <li key={i}>{[r.round_type, r.amount_usd ? usd(r.amount_usd) : null, r.announced_on ? day(r.announced_on) : null].filter(Boolean).join(", ")}
                            {r.investors.length ? ` from ${r.investors.join(", ")}` : ""} <a className="link text-xs" href={r.source_url} target="_blank" rel="noopener noreferrer">{domain(r.source_url)}</a></li>))}
                      </ul>
                    )}
                    <Link href={`/company/${answer.verified.entity_id}`} onClick={() => setOpen(false)} className="link text-sm w-fit" data-testid="ask-open-profile">Open the full verified profile</Link>
                  </section>
                ) : (
                  <p className="text-sm text-ink-muted" data-testid="ask-not-in-db">Not in the screener database yet, so nothing below has been verified by the screener.</p>
                )}

                {answer.registry && (
                  <section className="rounded-card border border-border p-4 grid gap-1 text-sm" data-testid="ask-registry">
                    <h3 className="font-semibold flex items-center gap-2"><Landmark className="w-4 h-4" aria-hidden />MCA company master (data.gov.in)</h3>
                    <p className="font-medium">{answer.registry.name}</p>
                    <dl className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      {[["CIN", answer.registry.cin ?? "-"], ["Status", answer.registry.status ?? "-"], ["Registered", day(answer.registry.registered_on)],
                        ["Paid-up capital", inr(answer.registry.paid_up_capital_inr)], ["Authorised capital", inr(answer.registry.authorized_capital_inr)],
                        ["State", answer.registry.location ?? "-"]].map(([k, v]) => (
                        <div key={k}><dt className="text-ink-muted text-xs">{k}</dt><dd className="font-medium break-all">{v}</dd></div>))}
                    </dl>
                  </section>
                )}

                {!answer.insights && <section className="rounded-card border border-border p-4 grid gap-2" data-testid="ask-web">
                  <h3 className="text-sm font-semibold flex flex-wrap items-center gap-2"><Globe2 className="w-4 h-4 text-info" aria-hidden />Web research (Gemini with Google Search)
                    <span className="chip bg-warning-subtle text-warning font-normal">Not verified by the screener</span>
                    {answer.web?.cached && (
                      <span className="chip bg-surface-alt text-ink-muted font-normal" data-testid="ask-cached"
                            title="Repeat questions are answered from a 24-hour cache, so they cost nothing">
                        {answer.web.cached_age_hours ? `Saved answer from ${answer.web.cached_age_hours}h ago` : "Saved answer from the last hour"}, no new search
                      </span>
                    )}
                    {answer.web && !webLoading && (
                      <button type="button" className="btn-ghost h-7 px-2 text-xs ml-auto" data-testid="ask-refresh"
                              onClick={() => lastAsk.current && void ask(lastAsk.current.q, lastAsk.current.entityId, true)}>
                        Refresh
                      </button>
                    )}</h3>
                  {webLoading ? (
                    <div className="grid gap-2" role="status" data-testid="ask-web-loading">
                      <p className="text-sm text-ink-muted flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" aria-hidden />
                        Searching the web and reading sources... {elapsed}s</p>
                      <div className="h-28 rounded-card bg-surface-alt animate-pulse" />
                    </div>
                  ) : answer.web ? (
                    <>
                      <WebText text={answer.web.text} sources={answer.web.sources} />
                      {answer.web.sources.length > 0 && (
                        <ol className="grid gap-0.5 text-xs text-ink-muted pt-2 border-t border-border" data-testid="ask-sources">
                          {answer.web.sources.map((s) => (
                            <li key={s.n}><span className="tabular">[{s.n}]</span>{" "}
                              <a className="link inline-flex items-center gap-1" href={s.url} target="_blank" rel="noopener noreferrer">{s.title}<ExternalLink className="w-3 h-3" aria-hidden /></a></li>))}
                        </ol>
                      )}
                    </>
                  ) : <p className="text-sm text-ink-muted" data-testid="ask-web-error">{answer.web_error ?? "No web answer."}</p>}
                </section>}
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** A button anywhere on the page that opens the search / ask box (optionally pre-filled). */
export function AskButton({ label = "Ask about a founder or company", query, className = "btn-primary" }: { label?: string; query?: string; className?: string }) {
  return (
    <button type="button" className={className} data-testid="ask-cta"
            onClick={() => window.dispatchEvent(new CustomEvent("open-ask", { detail: query }))}>
      <Sparkles className="w-4 h-4" aria-hidden />{label}
    </button>
  );
}
