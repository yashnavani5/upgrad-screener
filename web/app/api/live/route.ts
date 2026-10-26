import { connection } from "next/server";

import { getLive } from "@/lib/queries";

// Live status for the dashboard (polled by SWR): latest + active run, provider activity, universe counts.
export async function GET() {
  await connection(); // request-time only (live data)
  try {
    return Response.json(await getLive(), { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    console.error("[api/live]", err);
    return Response.json({ detail: "Database unreachable." }, { status: 503 });
  }
}
