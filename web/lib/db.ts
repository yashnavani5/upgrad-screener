import "server-only";

import postgres from "postgres";

// Read-only role (web_ro): SELECT on v_* views only. Never imported by client components.
const url = process.env.DATABASE_URL_RO;

declare global {
  var __screenerSql: ReturnType<typeof postgres> | undefined;
}

export function sql(): ReturnType<typeof postgres> {
  if (!url) throw new Error("DATABASE_URL_RO is not set (web/.env.local)");
  // Reuse one pool across hot reloads in development.
  globalThis.__screenerSql ??= postgres(url, { max: 5, idle_timeout: 30, connect_timeout: 10, prepare: false });
  return globalThis.__screenerSql;
}

/** Dates become ISO strings so rows match the JSON contracts (and serialize to client components). */
export function plain<T>(value: T): T {
  if (value instanceof Date) return value.toISOString() as unknown as T;
  if (Array.isArray(value)) return value.map(plain) as unknown as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, plain(v)])) as T;
  }
  return value;
}
