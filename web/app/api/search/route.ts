import { connection } from "next/server";

import { searchEntities } from "@/lib/queries";

// Type-ahead: companies, founders and registry entries matching the text (v_search).
export async function GET(request: Request) {
  await connection();
  const q = new URL(request.url).searchParams.get("q") ?? "";
  try {
    return Response.json({ q, hits: await searchEntities(q, 10) }, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    console.error("[api/search]", err);
    return Response.json({ detail: "Database unreachable." }, { status: 503 });
  }
}
