// Questions the screener can answer from its own verified data (no Gemini): "best fit founder", "top 5 acquire in
// India", "founders with an exit", "who raised the most". Anything else is treated as a name to look up.
export type Fit = "acquire" | "partner" | "co_build";
export type Intent = {
  kind: "shortlist" | "exits" | "raised";
  fit?: Fit;
  region?: "india" | "global";
  limit: number;
  title: string;
};

const QUESTION = /\?|^(who|what|which|whom|show|list|give|tell|find|recommend|suggest)\b|\b(best|top|strongest|most|shortlist|ranked?|recommend(ed)?|picks?|should we)\b|\b(founders?|companies|startups) (with|who)\b|\bprior exits?\b/i;
const LEADING = /^(please\s+)?(tell me (about|more about)|who is|who's|what is|what's|info (on|about)|information (on|about)|details (on|of|about)|about|look ?up|search( for)?|find)\s+/i;

/** "Tell me about Arivihan" -> "Arivihan" (for the name search). */
export function nameFrom(q: string): string {
  return q.trim().replace(LEADING, "").replace(/[?.!]+$/, "").trim();
}

export function parseIntent(q: string): Intent | null {
  const t = q.toLowerCase();
  if (!QUESTION.test(q)) return null;
  const fit: Fit | undefined = /\bco[- ]?build|\bbuild with\b|\bincubat/.test(t) ? "co_build"
    : /\bacqui(re|sition)|\bbuy\b|\bm ?& ?a\b/.test(t) ? "acquire"
      : /\bpartner/.test(t) ? "partner" : undefined;
  const region = /\bindia|\bindian\b/.test(t) ? "india" : /\bglobal|\boutside india|\binternational|\bforeign/.test(t) ? "global" : undefined;
  const n = t.match(/\b(?:top|best)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:best|top|founders|companies|picks)\b/);
  const limit = Math.min(15, Math.max(1, Number(n?.[1] ?? n?.[2] ?? (/\bthe best\b|\bbest (fit )?(founder|company|pick)\b/.test(t) ? 3 : 5))));
  const where = [fit && { acquire: "to acquire", partner: "to partner with", co_build: "to co-build with" }[fit],
    region === "india" ? "in India" : region === "global" ? "globally" : null].filter(Boolean).join(" ");
  if (/\bexit|\bexited|\bsold (a|their) (company|startup)|\bacquired before\b/.test(t)) {
    return { kind: "exits", fit, region, limit: 15, title: "Shortlisted founders with a verified prior exit" };
  }
  if (/\braised the most|\bmost (funded|funding|money)|\bbiggest (round|funding)|\bhighest funding/.test(t)) {
    return { kind: "raised", fit, region, limit, title: `Shortlisted companies by verified funding${where ? ` ${where}` : ""}` };
  }
  if (/\b(best|top|strongest|recommend|shortlist|picks?|fit|should we|rank)/.test(t)) {
    return { kind: "shortlist", fit, region, limit, title: `The screener's top ${limit}${where ? ` ${where}` : ""}` };
  }
  return null;
}
