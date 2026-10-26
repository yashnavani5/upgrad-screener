import { connection } from "next/server";

import { activeFitLabels } from "@/lib/fit-labels";

// Fit-category names and the enabled categories of the active AI Policy version.
export async function GET() {
  await connection();
  return Response.json(await activeFitLabels(), { headers: { "Cache-Control": "no-store" } });
}
