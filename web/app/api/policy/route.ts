import { connection } from "next/server";

import { policyCli } from "@/lib/policy-cli";
import { getPolicyVersions } from "@/lib/queries";

// GET: the active policy (or the YAML default), the default, and every saved version.
export async function GET() {
  await connection();
  const [show, versions] = await Promise.all([
    policyCli<{ version: number | null; policy: unknown; default: unknown }>(["policy", "show"]),
    getPolicyVersions().catch(() => []),
  ]);
  if (!show.ok) return Response.json({ detail: show.detail, versions }, { status: show.status });
  return Response.json({ ...show.data, versions }, { headers: { "Cache-Control": "no-store" } });
}
