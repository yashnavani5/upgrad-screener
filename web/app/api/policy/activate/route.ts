import { connection } from "next/server";

import { policyCli, startRescore } from "@/lib/policy-cli";
import { getActiveRun } from "@/lib/queries";

// POST {version, rescore}: make an earlier version active again (rollback); optionally re-score.
export async function POST(request: Request) {
  await connection();
  const body = (await request.json().catch(() => null)) as { version?: number; rescore?: boolean } | null;
  if (!body?.version || !Number.isInteger(body.version)) return Response.json({ detail: "Missing version." }, { status: 400 });
  const r = await policyCli<{ version: number }>(["policy", "activate", "--version", String(body.version)]);
  if (!r.ok) return Response.json({ detail: r.detail }, { status: r.status });
  let rescore: string | null = null;
  if (body.rescore) {
    if (await getActiveRun().catch(() => null)) rescore = "A run is in progress; re-score after it finishes.";
    else { const s = startRescore(); rescore = s.ok ? `Re-score started (log: ${s.data.log}).` : s.detail; }
  }
  return Response.json({ version: body.version, rescore });
}
