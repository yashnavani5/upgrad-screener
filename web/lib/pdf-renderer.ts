import "server-only";

import type { Browser, Page } from "playwright-core";

// One warm headless Chromium page shared by PDF exports. Launching Chromium and opening a page costs ~15 s on a cold
// Windows host (~3.5 s per extra page); rendering on a warm page takes ~1.5 s. Exports run one at a time on that page,
// the browser closes after IDLE_MS without exports, and any failure discards it so the next export starts clean.
const IDLE_MS = 10 * 60_000;

type Warm = { browser: Browser; page: Page };
let warm: Promise<Warm> | null = null;
let queue: Promise<unknown> = Promise.resolve();
let idleTimer: ReturnType<typeof setTimeout> | null = null;

async function launch(): Promise<Warm> {
  const { chromium } = await import(/* turbopackIgnore: true */ "playwright-core");
  const browser = await chromium.launch();
  const user = process.env.DASHBOARD_USER, password = process.env.DASHBOARD_PASSWORD;
  const context = await browser.newContext({
    httpCredentials: user && password ? { username: user, password } : undefined,
    viewport: { width: 900, height: 1200 }, reducedMotion: "reduce",
  });
  return { browser, page: await context.newPage() };
}

async function discard(): Promise<void> {
  const current = warm;
  warm = null;
  if (current) await current.then((w) => w.browser.close()).catch(() => undefined);
}

/** Run `fn` on the shared warm page (serialized). */
export function withReportPage<T>(fn: (page: Page) => Promise<T>): Promise<T> {
  const job = queue.then(async () => {
    if (idleTimer) clearTimeout(idleTimer);
    warm ??= launch();
    let w: Warm;
    try {
      w = await warm;
      if (!w.browser.isConnected() || w.page.isClosed()) {
        await discard();
        warm = launch();
        w = await warm;
      }
    } catch (err) {
      await discard();
      throw err;
    }
    try {
      return await fn(w.page);
    } catch (err) {
      await discard();
      throw err;
    } finally {
      idleTimer = setTimeout(() => { void discard(); }, IDLE_MS);
    }
  });
  queue = job.catch(() => undefined);
  return job;
}
