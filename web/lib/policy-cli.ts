import "server-only";

import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, openSync } from "node:fs";
import path from "node:path";

// The AI Policy is written by the backend CLI (`screener policy ...`): the dashboard's database role is read-only.
// Local mode only (same switch as "Run now"); every command prints one "@@JSON@@{...}" line.

export type CliResult<T> = { ok: true; data: T } | { ok: false; status: number; detail: string; data?: unknown };

function env(): { repo: string; uv: string } | string {
  const allowed = process.env.NODE_ENV === "development" || process.env.ALLOW_LOCAL_DISPATCH === "true";
  if (!allowed) return "Policy editing needs the backend on this machine (ALLOW_LOCAL_DISPATCH=true).";
  const repo = process.env.SCREENER_REPO_DIR;
  if (!repo) return "SCREENER_REPO_DIR is not set.";
  return { repo, uv: process.env.UV_BIN || "uv" };
}

/** Run `screener <args>` with optional JSON on stdin; resolve with the marked JSON result. */
export function policyCli<T>(args: string[], stdin?: unknown, timeoutMs = 240_000): Promise<CliResult<T>> {
  const e = env();
  if (typeof e === "string") return Promise.resolve({ ok: false, status: 403, detail: e });
  return new Promise((resolve) => {
    const child = spawn(/*turbopackIgnore: true*/ e.uv, ["run", "screener", ...args], {
      cwd: e.repo, windowsHide: true, env: { ...process.env, PYTHONIOENCODING: "utf-8", COLUMNS: "200" },
    });
    let out = "";
    let err = "";
    const timer = setTimeout(() => { child.kill(); resolve({ ok: false, status: 504, detail: "The backend took too long." }); }, timeoutMs);
    child.stdout.on("data", (d) => { out += d.toString(); });
    child.stderr.on("data", (d) => { err += d.toString(); });
    child.on("error", (x) => { clearTimeout(timer); resolve({ ok: false, status: 502, detail: `Could not start the backend: ${x.message}` }); });
    child.on("close", (code) => {
      clearTimeout(timer);
      const line = out.split(/\r?\n/).reverse().find((l) => l.startsWith("@@JSON@@"));
      const data = line ? JSON.parse(line.slice("@@JSON@@".length)) : undefined;
      if (code === 0 && data !== undefined) resolve({ ok: true, data: data as T });
      else if (data && typeof data === "object" && "error" in (data as object)) resolve({ ok: false, status: 400, detail: String((data as { error: string }).error), data });
      else resolve({ ok: false, status: 500, detail: (err.trim().split(/\r?\n/).pop() || `backend exited with ${code}`).slice(0, 300) });
    });
    if (stdin !== undefined) child.stdin.end(JSON.stringify(stdin));
    else child.stdin.end();
  });
}

/** Start `screener rescore` in the background (score + explain under the active policy). */
export function startRescore(): CliResult<{ log: string }> {
  const e = env();
  if (typeof e === "string") return { ok: false, status: 403, detail: e };
  const logDir = path.join(/*turbopackIgnore: true*/ e.repo, ".cache", "logs");
  mkdirSync(/*turbopackIgnore: true*/ logDir, { recursive: true });
  const logFile = path.join(/*turbopackIgnore: true*/ logDir, `dashboard-rescore-${new Date().toISOString().replace(/[:.]/g, "-")}.log`);
  const out = openSync(/*turbopackIgnore: true*/ logFile, "a");
  const child = spawn(/*turbopackIgnore: true*/ e.uv, ["run", "screener", "rescore", "--trigger", "dashboard"], {
    cwd: e.repo, detached: true, stdio: ["ignore", out, out], windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: "utf-8", COLUMNS: "200" },
  });
  // Record how the process ended, so a launch that fails is never a silent empty log.
  child.on("exit", (code, signal) => { try { appendFileSync(/*turbopackIgnore: true*/ logFile, `
[dashboard] process exited with code ${code ?? signal}
`); } catch { /* log gone */ } });
  child.on("error", (e) => { try { appendFileSync(/*turbopackIgnore: true*/ logFile, `
[dashboard] could not start: ${e.message}
`); } catch { /* log gone */ } });
  child.unref();
  return { ok: true, data: { log: path.basename(logFile) } };
}
