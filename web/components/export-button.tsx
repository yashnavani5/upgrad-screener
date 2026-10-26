"use client";

import { Loader2 } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

function filenameFrom(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  return /filename="?([^";]+)"?/i.exec(cd)?.[1] ?? fallback;
}

// Downloads an export route with visible progress: disabled + spinner while the server works (the PDF takes several
// seconds), saves the file from the response, and reports failures instead of failing silently. If the PDF route
// falls back to the report's print view (no browser on the host), that page is opened instead of saved as a file.
export function ExportButton({ href, expect, fallbackName, className, testId, title, busyLabel, warmUrl, children }: {
  href: string; expect: "text/csv" | "application/pdf"; fallbackName: string; className: string; testId?: string;
  title?: string; busyLabel: string; warmUrl?: string; children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {  // best effort: start the server's PDF browser while the user reads the page
    if (warmUrl) fetch(warmUrl, { cache: "no-store" }).catch(() => undefined);
  }, [warmUrl]);

  async function run() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(href, { cache: "no-store" });
      if (!res.ok) throw new Error(`the server answered ${res.status}`);
      if (!(res.headers.get("content-type") ?? "").startsWith(expect)) {
        const target = new URL(res.url);
        if (target.origin !== window.location.origin) throw new Error("unexpected response");
        window.open(target.href, "_blank", "noopener");  // print-view fallback: the page opens its own Save as PDF dialog
        return;
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filenameFrom(res, fallbackName);
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (err) {
      setError(`Export failed: ${err instanceof Error ? err.message : String(err)}. Try again.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="relative inline-flex">
      <button type="button" onClick={run} disabled={busy} aria-busy={busy} className={`${className} disabled:opacity-70 disabled:cursor-wait`}
              data-testid={testId} title={title}>
        {busy ? <><Loader2 className="w-4 h-4 animate-spin" aria-hidden /><span>{busyLabel}</span></> : children}
      </button>
      {error && (
        <span role="alert" className="absolute right-0 top-full mt-1 z-10 w-64 card px-3 py-2 text-xs text-danger">{error}</span>
      )}
    </span>
  );
}
