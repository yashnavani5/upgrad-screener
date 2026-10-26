import { connection } from "next/server";

import { policyCli } from "@/lib/policy-cli";

// POST {policy}: shortlist under the current vs the draft policy (recomputed, no AI calls).
export async function POST(request: Request) {
  await connection();
  const body = (await request.json().catch(() => null)) as { policy?: unknown } | null;
  if (!body?.policy) return Response.json({ detail: "Missing policy." }, { status: 400 });
  const r = await policyCli(["policy", "preview"], body.policy, 300_000);
  return r.ok ? Response.json(r.data) : Response.json({ detail: r.detail, data: r.data }, { status: r.status });
}
