import { connection } from "next/server";

import { getActiveRun, getRun } from "@/lib/queries";

export async function GET() {
  await connection(); // request-time only (live data)
  try {
    const [active, latest] = await Promise.all([getActiveRun(), getRun()]);
    return Response.json({ active, latest }, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    console.error("[api/runs/latest]", err);
    return Response.json({ detail: "Database unreachable." }, { status: 503 });
  }
}
