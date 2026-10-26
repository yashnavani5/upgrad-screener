"use client";

import { Play, RefreshCw } from "lucide-react";
import { useState } from "react";

import { STAGE_LABEL } from "@/lib/format";
import { useLive } from "@/lib/use-live";

import { StatusPill } from "./ui";

/** Header: latest run status pill + "Run now". Disabled while a run is active; a second run gets a 409. */
export function RunControls() {
  const { data, error, mutate } = useLive();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const active = data?.active ?? null;
  const latest = data?.latest ?? null;

  async function runNow() {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ top_n: 150 }) });
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      setMessage({ tone: res.ok ? "ok" : "error", text: body.detail ?? (res.ok ? "Run started." : `Request failed (${res.status}).`) });
      // The pipeline writes its run row a few seconds after starting; poll until it shows up.
      for (const wait of [2000, 4000, 8000]) {
        await new Promise((r) => setTimeout(r, wait));
        const fresh = await mutate();
        if (fresh?.active) break;
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <div className="hidden md:flex items-center gap-2 text-sm" aria-live="polite">
        {error ? (
          <StatusPill status="failed" label="Database unreachable" />
        ) : active ? (
          <>
            <StatusPill status="running" label={`Running: ${STAGE_LABEL[active.current_stage ?? ""] ?? "starting"}`} />
            <span className="text-ink-muted tabular">{Math.round((active.stage_progress ?? 0) * 100)}%</span>
          </>
        ) : latest ? (
          <StatusPill status={latest.status} label={`Last run: ${latest.status}`} />
        ) : data ? (
          <StatusPill status="unknown" label="No runs yet" />
        ) : null}
      </div>
      <button type="button" className="btn-primary px-3 sm:px-4" onClick={runNow} disabled={busy || Boolean(active)}
              data-testid="run-now" title={active ? "A run is already in progress" : "Start a full pipeline run"}>
        {busy ? <RefreshCw className="w-4 h-4 animate-spin" aria-hidden /> : <Play className="w-4 h-4" aria-hidden />}
        {active
          ? <><span className="hidden sm:inline whitespace-nowrap">Run in progress</span><span className="sr-only sm:hidden">Run in progress</span></>
          : <><span className="hidden sm:inline whitespace-nowrap">Run now</span><span className="sr-only sm:hidden">Run now</span></>}
      </button>
      {message && (
        <div role="status" className={`fixed right-4 bottom-4 z-50 card animate-slide-up px-4 py-3 text-sm max-w-sm ${message.tone === "error" ? "border-danger text-danger" : ""}`}>
          {message.text}
          <button type="button" className="ml-3 link" onClick={() => setMessage(null)}>Dismiss</button>
        </div>
      )}
    </div>
  );
}
