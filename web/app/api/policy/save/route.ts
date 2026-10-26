import { connection } from "next/server";

import { policyCli, startRescore } from "@/lib/policy-cli";
import { getActiveRun } from "@/lib/queries";

// POST {policy, note, rescore}: store a new active version; optionally start a re-score under it.
export async function POST(request: Request) {
  await connection();
  const body = (await request.json().catch(() => null)) as { policy?: unknown; note?: string; rescore?: boolean } | null;
  if (!body?.policy) return Response.json({ detail: "Missing policy." }, { status: 400 });
  const note = (body.note ?? "").slice(0, 500);
  const saved = await policyCli<{ version: number }>(["policy", "save", "--author", process.env.DASHBOARD_USER || "dashboard",
    "--note", note || "edited on the dashboard"], body.policy);
  if (!saved.ok) return Response.json({ detail: saved.detail, data: saved.data }, { status: saved.status });
  let rescore: string | null = null;
  if (body.rescore) {
    if (await getActiveRun().catch(() => null)) rescore = "A run is in progress; re-score after it finishes.";
    else { const r = startRescore(); rescore = r.ok ? `Re-score started (log: ${r.data.log}).` : r.detail; }
  }
  return Response.json({ version: saved.data.version, rescore });
}
