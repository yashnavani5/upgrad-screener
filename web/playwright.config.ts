import { readFileSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

// Credentials come from web/.env.local (the same basic-auth pair the proxy enforces).
function env(name: string): string {
  if (process.env[name]) return process.env[name] as string;
  const line = readFileSync(".env.local", "utf8").split(/\r?\n/).find((l) => l.startsWith(`${name}=`));
  return line ? line.slice(name.length + 1).trim() : "";
}

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 2,
  reporter: [["list"]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3001",
    httpCredentials: { username: env("DASHBOARD_USER"), password: env("DASHBOARD_PASSWORD") },
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile", use: { ...devices["Pixel 5"], viewport: { width: 375, height: 812 } } },
  ],
});
