import { NextResponse, type NextRequest } from "next/server";

/**
 * Content-Security-Policy, with a fresh nonce on every request.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * WHY THIS IS NOT IN CADDY LIKE EVERY OTHER SECURITY HEADER.
 *
 * deploy/business.caddy sets HSTS, X-Frame-Options, Referrer-Policy and the
 * rest for the whole host, and it deliberately carried no CSP: "absent until
 * there is a frontend to test it against."
 *
 * There is one now, and it cannot be done from Caddy. Next renders its flight
 * data into inline <script> blocks — 37 of them across the screens — and their
 * contents change on every render, so a hash cannot pin them. The remaining
 * choices are 'unsafe-inline', which gives away most of what a CSP is for, or
 * a per-request nonce, which only the thing rendering the HTML can mint.
 *
 * So the host is split the same way the routing already splits it:
 *
 *   /auth /org /brn /ctl /invt /sls /admin /static   →  Caddy's policy
 *   everything else (this application)               →  this one
 *
 * Two applications behind one hostname genuinely need two policies. Django's
 * three templates carry no inline script and no inline style — there is a test
 * pinning that — so its policy needs no nonce and is stricter than this.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── HOW THE NONCE REACHES NEXT'S OWN SCRIPT TAGS ──────────────────────────
 *
 * Next reads the `Content-Security-Policy` REQUEST header, pulls the nonce out
 * of it, and stamps that nonce onto every script it emits. So the header has
 * to go on the request as well as the response — setting only the response
 * produces a policy whose nonce matches nothing, and a blank screen.
 *
 * ⚠ IT ONLY WORKS FOR DYNAMICALLY RENDERED PAGES. A prerendered page is built
 *   once and served to everybody, so it cannot carry a per-request value.
 *   /till was `force-static` and had to become dynamic for this reason — see
 *   the note there.
 */

/**
 * ── REPORT-ONLY FIRST, WHICH IS WHAT business.caddy ASKS FOR ──────────────
 *
 * "ship it Report-Only first, walk every screen with the console open, then
 * rename the header." The failure mode of a wrong CSP is a blank screen nobody
 * notices until they open the one page that used the blocked thing, and this
 * application has a till on it.
 *
 * Flip to false to enforce. Nothing else changes.
 */
const REPORT_ONLY = true;

function policy(nonce: string): string {
  return [
    // Everything that is not named below falls here, and 'self' is the whole
    // truth for this application: no CDN, no analytics, no embedded anything.
    "default-src 'self'",

    /*
     * 'strict-dynamic' is what makes the nonce practical. Next emits a few
     * nonce'd bootstrap scripts which then load the chunk files themselves;
     * without it every chunk would need its own nonce, which nothing can do.
     * With it, a script trusted by nonce may load more — and the 'self' is
     * ignored by browsers that understand strict-dynamic, kept for those that
     * do not.
     */
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,

    /*
     * No 'unsafe-inline'. Checked across all fourteen screens: zero style=""
     * attributes and zero <style> blocks in the served HTML — CSS modules all
     * compile to files.
     *
     * ⚠ THE NONCE IS HERE FOR A REASON FOUND BY READING THE BUNDLE, NOT THE
     *   MARKUP. Next creates <style> elements at runtime for route-level CSS
     *   and React 19 does the same for hoisted stylesheets; both stamp the
     *   nonce onto them when one is available
     *   (`r && e.setAttribute("nonce", r)`). With `style-src 'self'` alone
     *   those elements carry a nonce the policy does not list, and a browser
     *   blocks them — which the static HTML cannot show, because they do not
     *   exist until the page runs.
     */
    `style-src 'self' 'nonce-${nonce}'`,

    // next/font self-hosts, so fonts are same-origin too.
    "font-src 'self'",

    // data: for the favicon and any inlined mark. No remote images: a shop's
    // dashboard has no business fetching pictures from anywhere else.
    "img-src 'self' data:",

    /*
     * Server actions post here, and the till fetches Django through the same
     * origin — Caddy splits by path, so /sls/sales/checkout/ IS 'self'. That
     * is the property next.config.ts calls load-bearing for the session
     * cookie, and it pays again here.
     */
    "connect-src 'self'",

    // Where a <form> may send. Server actions target this origin; nothing
    // posts anywhere else, and sign-in leaves by a link rather than a form.
    "form-action 'self'",

    // The same statement X-Frame-Options makes, in the header that supersedes
    // it. A POS dashboard inside somebody else's iframe collects clicks that
    // move stock and cash.
    "frame-ancestors 'none'",
    "frame-src 'none'",

    // Nothing to embed and nothing to plug in.
    "object-src 'none'",

    // Stops an injected <base> silently repointing every relative URL on the
    // page at somebody else's host.
    "base-uri 'none'",

    /*
     * No 'unsafe-eval', deliberately. The only `Function("return this")` in
     * the bundle is the globalThis polyfill, which checks
     * `typeof globalThis` first and sits inside a try/catch — so on any
     * browser this application supports it is never reached, and a block
     * would be caught if it were.
     */
  ].join("; ");
}

export function middleware(request: NextRequest) {
  // crypto.randomUUID is available in the middleware runtime and needs no
  // import. 16 bytes of randomness, base64 — the nonce only has to be
  // unguessable within one response.
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const header = REPORT_ONLY
    ? "Content-Security-Policy-Report-Only"
    : "Content-Security-Policy";
  const value = policy(nonce);

  // On the REQUEST, so Next can read the nonce back out and stamp its own
  // script tags with it. Without this the policy is real and matches nothing.
  const forwarded = new Headers(request.headers);
  forwarded.set("x-nonce", nonce);
  forwarded.set("Content-Security-Policy", value);

  const response = NextResponse.next({ request: { headers: forwarded } });
  response.headers.set(header, value);
  return response;
}

export const config = {
  /*
   * Skip the things that are not documents. /_next/static is hashed and
   * immutable, images are images, and a policy on them protects nothing while
   * costing a middleware invocation on every asset of every page load.
   *
   * The API paths are absent because they never reach this application at all
   * — Caddy routes them to Django before Next sees them.
   */
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|woff2?)$).*)",
  ],
};
