import "server-only";

import { spawn } from "node:child_process";
import { appendFileSync, mkdirSync, openSync } from "node:fs";
import path from "node:path";

// "Run now" dispatcher (docs/ARCHITECTURE.md section 6).
//   local  (Phase 1): start `screener run` on this machine as a detached background process.
//   github (Phase 2): POST a workflow_dispatch to GitHub Actions.

export type DispatchResult = { ok: true; detail: string } | { ok: false; status: number; detail: string };

export async function dispatch(topN: number): Promise<DispatchResult> {
  const mode = process.env.RUN_DISPATCH_MODE ?? "local";
  return mode === "github" ? dispatchGithub(topN) : dispatchLocal(topN);
}

function dispatchLocal(topN: number): DispatchResult {
  const allowed = process.env.NODE_ENV === "development" || process.env.ALLOW_LOCAL_DISPATCH === "true";
  if (!allowed) return { ok: false, status: 403, detail: "Local dispatch is disabled on this server." };
  const repo = process.env.SCREENER_REPO_DIR;
  if (!repo) return { ok: false, status: 500, detail: "SCREENER_REPO_DIR is not set." };
  const uv = process.env.UV_BIN || "uv";
  const logDir = path.join(/*turbopackIgnore: true*/ repo, ".cache", "logs");
  mkdirSync(/*turbopackIgnore: true*/ logDir, { recursive: true });
  const logFile = path.join(/*turbopackIgnore: true*/ logDir, `dashboard-run-${new Date().toISOString().replace(/[:.]/g, "-")}.log`);
  const out = openSync(/*turbopackIgnore: true*/ logFile, "a");
  try {
    const child = spawn(/*turbopackIgnore: true*/ uv, ["run", "screener", "run", "--top", String(topN), "--trigger", "dashboard"], {
      cwd: repo,
      detached: true,
      stdio: ["ignore", out, out],
      windowsHide: true,
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
    return { ok: true, detail: `Run started on this machine (log: ${path.basename(logFile)}).` };
  } catch (err) {
    return { ok: false, status: 502, detail: `Could not start the pipeline: ${(err as Error).message}` };
  }
}

async function dispatchGithub(topN: number): Promise<DispatchResult> {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  const repo = process.env.GITHUB_REPO;
  if (!token || !repo) return { ok: false, status: 500, detail: "GITHUB_DISPATCH_TOKEN / GITHUB_REPO are not set." };
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
  // A queued or running workflow counts as an active run (covers the gap before the job writes its runs row).
  const active = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/screener.yml/runs?per_page=5`, { headers });
  if (active.ok) {
    const body = (await active.json()) as { workflow_runs?: { status: string }[] };
    if (body.workflow_runs?.some((r) => r.status === "queued" || r.status === "in_progress")) {
      return { ok: false, status: 409, detail: "A run is already in progress on GitHub Actions." };
    }
  }
  const res = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/screener.yml/dispatches`, {
    method: "POST",
    headers,
    body: JSON.stringify({ ref: process.env.GITHUB_REF || "main", inputs: { top_n: String(topN) } }),
  });
  if (res.status === 204) return { ok: true, detail: "Run requested on GitHub Actions." };
  return { ok: false, status: 502, detail: `GitHub returned ${res.status}.` };
}
