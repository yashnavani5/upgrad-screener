// Display helpers. House style: no em or en dashes anywhere in the UI. Text quoted from source pages keeps its
// exact wording in the database (grounding needs it), but is displayed with plain hyphens.

const DASH = /\s*[—–]\s*/g;

export function clean(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(DASH, " - ").replace(/\s{2,}/g, " ").trim();
}


export const REASON_LABEL: Record<string, string> = {
  TOO_LARGE: "Too large",
  COMPETITOR: "Competitor at scale",
  UPGRAD_OWNED: "Already upGrad",
  UNVERIFIABLE: "Not verifiable",
  INACTIVE: "Inactive",
  LOW_FIT: "Low fit",
  NOT_EDUCATION: "Not education",
  DUPLICATE_THESIS: "Duplicate thesis",
  ACQUIRED: "Acquired",
  POLICY_FILTER: "Policy filter",
};

export const STAGES = ["discover", "resolve", "enrich", "verify", "score", "explain", "publish"] as const;
export const STAGE_LABEL: Record<string, string> = {
  discover: "Discover", resolve: "Resolve", enrich: "Enrich", verify: "Verify", score: "Score",
  explain: "Explain", publish: "Publish",
};

export function score(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "-" : v.toFixed(digits);
}

export function count(v: number | null | undefined): string {
  return v === null || v === undefined ? "-" : new Intl.NumberFormat("en-IN").format(v);
}

export function usd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "-";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(v);
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(new Date(iso));
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(iso));
}

export function duration(start: string | null | undefined, end: string | null | undefined): string {
  if (!start) return "-";
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime();
  const m = Math.max(0, Math.round(ms / 60000));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function domain(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function runQuery(run: string | null | undefined): string {
  return run ? `?run=${encodeURIComponent(run)}` : "";
}
