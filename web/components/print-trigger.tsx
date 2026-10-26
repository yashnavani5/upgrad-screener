"use client";

import { useEffect } from "react";

/** Opens the browser's print dialog once the report has rendered (fallback when the server cannot make the PDF). */
export function PrintTrigger({ enabled }: { enabled: boolean }) {
  useEffect(() => {
    if (!enabled) return;
    const t = setTimeout(() => window.print(), 600);
    return () => clearTimeout(t);
  }, [enabled]);
  return null;
}
