import { NextResponse } from "next/server";

// Crossr is private and hidden. When CROSSR_LOCK=1 and CROSSR_ENTRY_PATH are set on the web
// service, every page answers a bare 404 "Site not found" unless the visitor has an approved,
// signed-in session (checked with the API, crossr_core/access.py). The only way in is the
// owner's secret sign-in address, CROSSR_ENTRY_PATH (for example /enter-<random>), which shows
// the sign-in form; approved people create their account there with the invited email.
const API = process.env.API_INTERNAL || "http://127.0.0.1:8000";
const ENTRY = (process.env.CROSSR_ENTRY_PATH || "").replace(/\/+$/, "");
const NOINDEX = { "X-Robots-Tag": "noindex, nofollow, noarchive", "Referrer-Policy": "no-referrer" };
// one-time guest links: /guest/<token> (crossr_core/guest_pass.py); the token itself is checked by the API
const GUEST = /^\/guest\/[A-Za-z0-9_-]{20,100}$/;

// session token -> [checked at (ms), approved?]; a minute per visitor, not one API call per page
const seen = new Map();
const TTL = 60_000;

function notFound() {
  return new NextResponse("Site not found", {
    status: 404,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store", ...NOINDEX },
  });
}

function pass() {
  const res = NextResponse.next();
  for (const [k, v] of Object.entries(NOINDEX)) res.headers.set(k, v);
  return res;
}

async function approved(token) {
  const now = Date.now();
  const hit = seen.get(token);
  if (hit && now - hit[0] < TTL) return hit[1];
  let ok = false;
  try {
    const res = await fetch(`${API}/api/auth/me`, { headers: { cookie: `crossr_session=${token}` }, cache: "no-store" });
    ok = res.ok && Boolean((await res.json()).user);
  } catch {
    ok = false;
  }
  if (seen.size > 5000) seen.clear();
  seen.set(token, [now, ok]);
  return ok;
}

export async function middleware(request) {
  if (process.env.CROSSR_LOCK !== "1" || !ENTRY.startsWith("/") || ENTRY.length < 8) return pass();
  const { pathname } = request.nextUrl;
  if (pathname === ENTRY) {
    // the secret sign-in address: show the sign-in form without changing the URL
    const url = request.nextUrl.clone();
    url.pathname = "/signin";
    const res = NextResponse.rewrite(url);
    for (const [k, v] of Object.entries(NOINDEX)) res.headers.set(k, v);
    return res;
  }
  if (GUEST.test(pathname)) return pass();
  const token = request.cookies.get("crossr_session")?.value;
  if (token && (await approved(token))) return pass();
  return notFound();
}

export const config = {
  // pages only: not the API proxy (the API hides itself), Next's own files, or images
  matcher: ["/((?!api/|_next/|favicon|.*\\.(?:png|jpg|jpeg|svg|ico|webp|gif|txt|xml)$).*)"],
};
