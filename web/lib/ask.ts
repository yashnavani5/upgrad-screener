import "server-only";

import { GoogleGenAI } from "@google/genai";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

// "Ask" box: Gemini with Google Search grounding for what the screener has not verified yet. The answer is shown
// as web research with its sources, never mixed with the screener's verified claims.
export type WebSource = { n: number; title: string; url: string };
export type WebAnswer = {
  text: string; sources: WebSource[]; queries: string[]; model: string; ms: number;
  cached?: boolean; cached_at?: string;   // served from the 24 h answer cache (no Gemini call)
  cached_age_hours?: number;
};

// --- answer cache: a repeat question never spends Gemini again within ASK_CACHE_HOURS (default 24) ----------
const CACHE_DIR = process.env.ASK_CACHE_DIR
  ?? path.join(/*turbopackIgnore: true*/ process.env.SCREENER_REPO_DIR ?? path.join(process.cwd(), ".."), ".cache", "ask");
const CACHE_HOURS = Number(process.env.ASK_CACHE_HOURS ?? 24);

function cacheFile(query: string, hint?: string): string {
  const key = `${query.trim().toLowerCase().replace(/\s+/g, " ")}|${(hint ?? "").toLowerCase()}`;
  return path.join(/*turbopackIgnore: true*/ CACHE_DIR, `${createHash("sha256").update(key).digest("hex").slice(0, 32)}.json`);
}

async function fromCache(query: string, hint?: string): Promise<WebAnswer | null> {
  try {
    const hit = JSON.parse(await readFile(/*turbopackIgnore: true*/ cacheFile(query, hint), "utf8")) as WebAnswer & { cached_at: string };
    const ageHours = (Date.now() - Date.parse(hit.cached_at)) / 3_600_000;
    return ageHours <= CACHE_HOURS ? { ...hit, cached: true, cached_age_hours: Math.max(0, Math.round(ageHours)) } : null;
  } catch {
    return null; // no entry (or unreadable): ask Gemini
  }
}

async function toCache(query: string, hint: string | undefined, answer: WebAnswer): Promise<void> {
  try {
    await mkdir(/*turbopackIgnore: true*/ CACHE_DIR, { recursive: true });
    await writeFile(/*turbopackIgnore: true*/ cacheFile(query, hint), JSON.stringify({ ...answer, cached_at: new Date().toISOString() }), "utf8");
  } catch (err) {
    console.error("[ask] cache write failed", err); // caching is best effort
  }
}

const SYSTEM = `You research founders and education companies for upGrad's partnerships, M&A and co-build team.
Use Google Search. Answer in plain text with exactly these headings, each followed by 1 to 4 lines starting with "- ":
About the founder(s)
About the company
Financials
Recent news
Possible fit for upGrad
Financials covers funding rounds (date, amount with currency, investors), total raised, revenue (fiscal year), valuation
and registration details when public. Rules: state only facts found in the search results; always give the year;
if something is not public write "Not found in public sources"; never estimate or invent numbers, names or dates;
if the name is ambiguous, say which entity you describe. No em dashes, no markdown bold.`;

let client: GoogleGenAI | null = null;

function gemini(): GoogleGenAI | null {
  const key = process.env.GEMINI_API_KEY;
  if (!key) return null;
  client ??= new GoogleGenAI({ apiKey: key });
  return client;
}

/** Insert [n] citation markers at the end of each grounded segment (indices are UTF-8 byte offsets). */
function cite(text: string, supports: { segment?: { endIndex?: number }; groundingChunkIndices?: number[] }[]): string {
  const bytes = Buffer.from(text, "utf8");
  const marks = supports
    .filter((s) => s.segment?.endIndex !== undefined && s.groundingChunkIndices?.length)
    .map((s) => ({ at: s.segment!.endIndex!, label: [...new Set(s.groundingChunkIndices!)].map((i) => `[${i + 1}]`).join("") }))
    .sort((a, b) => b.at - a.at);
  let out = bytes;
  for (const m of marks) {
    if (m.at > out.length) continue;
    out = Buffer.concat([out.subarray(0, m.at), Buffer.from(m.label, "utf8"), out.subarray(m.at)]);
  }
  return out.toString("utf8");
}

export async function askWeb(query: string, hint?: string, { timeoutMs = 45_000, refresh = false } = {}): Promise<WebAnswer | null> {
  if (!refresh) {
    const hit = await fromCache(query, hint);
    if (hit) return hit;
  }
  const ai = gemini();
  const model = process.env.GEMINI_MODEL_FAST;
  if (!ai || !model) return null;
  const started = Date.now();
  const prompt = `Research: ${query}` + (hint ? `\nMost likely this entity (from our database): ${hint}` : "");
  const call = ai.models.generateContent({
    model, contents: prompt, config: { systemInstruction: SYSTEM, tools: [{ googleSearch: {} }], temperature: 0.2 },
  });
  const timeout = new Promise<never>((_, reject) => setTimeout(() => reject(new Error("timeout")), timeoutMs));
  const res = await Promise.race([call, timeout]);
  const meta = res.candidates?.[0]?.groundingMetadata;
  const sources = (meta?.groundingChunks ?? []).map((c, i) => ({ n: i + 1, title: c.web?.title ?? "source", url: c.web?.uri ?? "" }))
    .filter((s) => s.url);
  const text = cite((res.text ?? "").replace(/\s*[—–]\s*/g, ", ").replace(/\*\*/g, ""), meta?.groundingSupports ?? []);
  const answer = { text, sources, queries: meta?.webSearchQueries ?? [], model, ms: Date.now() - started };
  if (text.trim() && sources.length) await toCache(query, hint, answer); // only real, sourced answers are cached
  return answer;
}
