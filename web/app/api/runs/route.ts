import { connection } from "next/server";

import { dispatch } from "@/lib/dispatch";
import { getActiveRun } from "@/lib/queries";

// "Run now": refuse while a run is active (409), otherwise dispatch (local process or GitHub Actions).
export async function POST(request: Request) {
  await connection();
  const body = (await request.json().catch(() => ({}))) as { top_n?: number };
  const topN = Math.max(1, Math.min(1000, Math.round(Number(body.top_n) || 150)));
  try {
    const active = await getActiveRun();
    if (active) {
      return Response.json({ detail: `A run is already in progress (${active.current_stage ?? "starting"}).` }, { status: 409 });
    }
  } catch (err) {
    console.error("[api/runs]", err);
    return Response.json({ detail: "Database unreachable." }, { status: 503 });
  }
  const result = await dispatch(topN);
  return result.ok
    ? Response.json({ detail: result.detail }, { status: 202 })
    : Response.json({ detail: result.detail }, { status: result.status });
}
