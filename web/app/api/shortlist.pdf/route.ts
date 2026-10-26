import { connection } from "next/server";

import { withReportPage } from "@/lib/pdf-renderer";
import { reportQuery } from "@/lib/report";

// PDF export: renders /shortlist/report (same filters as the page and the CSV) in headless Chromium and returns an
// A4 PDF with page numbers and clickable source links, on a warm shared page (lib/pdf-renderer.ts). Where no browser
// is available (e.g. a serverless host), the user is sent to the report page, which opens "Save as PDF" instead.
export async function GET(request: Request) {
  await connection();
  const url = new URL(request.url);
  if (url.searchParams.get("warm") === "1") {  // the Shortlist page pre-starts Chromium so the first export is fast
    await withReportPage(async () => undefined).catch(() => undefined);
    return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
  }
  const q = reportQuery({
    run: url.searchParams.get("run") ?? undefined, region: url.searchParams.get("region") ?? undefined,
    fit: url.searchParams.getAll("fit"), min_confidence: url.searchParams.get("min_confidence") ?? undefined,
  });
  const target = `${url.origin}/shortlist/report?${q.toString()}`;
  try {
    const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
    const pdf = await withReportPage(async (page) => {
      // Not "networkidle": the app shell polls /api/live, so the network is rarely idle and that wait costs seconds.
      // Wait for what the PDF actually needs instead: the streamed report, web fonts and images.
      const res = await page.goto(target, { waitUntil: "load", timeout: 60_000 });
      if (!res?.ok()) throw new Error(`report page returned ${res?.status()}`);
      await page.getByTestId("report").waitFor({ timeout: 30_000 });
      await page.evaluate(async () => {
        await document.fonts.ready;
        await Promise.all(Array.from(document.images).filter((img) => !img.complete)
          .map((img) => new Promise((done) => { img.onload = img.onerror = done; })));
      });
      return page.pdf({
        format: "A4", printBackground: true, scale: 0.85, margin: { top: "14mm", bottom: "16mm", left: "11mm", right: "11mm" },
        displayHeaderFooter: true,
        headerTemplate: "<span></span>",
        footerTemplate: `<div style="font-size:8px;width:100%;padding:0 11mm;color:#6b7280;display:flex;justify-content:space-between;font-family:sans-serif">
        <span>Founder Screener for upGrad. Generated ${stamp} UTC. Every fact links to its source.</span>
        <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>`,
      });
    });
    const name = `upgrad-shortlist-${url.searchParams.get("region") ?? "global"}-${new Date().toISOString().slice(0, 10)}.pdf`;
    return new Response(new Uint8Array(pdf), {
      headers: { "Content-Type": "application/pdf", "Content-Disposition": `attachment; filename="${name}"`, "Cache-Control": "no-store" },
    });
  } catch (err) {
    console.error("pdf export failed, falling back to print view:", err);
    q.set("print", "1");
    return Response.redirect(`${url.origin}/shortlist/report?${q.toString()}`, 303);
  }
}
