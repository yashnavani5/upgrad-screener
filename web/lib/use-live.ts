"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import useSWR from "swr";

import type { Live } from "./queries";

const fetcher = async (url: string) => {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as Live;
};

/** Live status for all client panels: every 5 s while a run is active, otherwise every 60 s.
 *  When an active run finishes, server-rendered pages are refreshed so tables show the new results. */
export function useLive() {
  const router = useRouter();
  const swr = useSWR("/api/live", fetcher, {
    refreshInterval: (data) => (data?.active ? 5000 : 60000),
    revalidateOnFocus: true,
  });
  const wasActive = useRef<boolean | null>(null);
  const active = Boolean(swr.data?.active);
  useEffect(() => {
    if (wasActive.current === true && !active) router.refresh();
    wasActive.current = active;
  }, [active, router]);
  return swr;
}
