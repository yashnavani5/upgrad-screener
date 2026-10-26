import { NextResponse, type NextRequest } from "next/server";

// HTTP basic auth on every page and API route (DASHBOARD_USER / DASHBOARD_PASSWORD). Fails closed.
export function proxy(request: NextRequest) {
  const user = process.env.DASHBOARD_USER;
  const pass = process.env.DASHBOARD_PASSWORD;
  if (!user || !pass) {
    return new NextResponse("Dashboard credentials are not configured.", { status: 503 });
  }
  const header = request.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    const [u, ...rest] = atob(header.slice(6)).split(":");
    if (u === user && rest.join(":") === pass) return NextResponse.next();
  }
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Founder Screener", charset="UTF-8"' },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
