import type { NextConfig } from "next";

/**
 * Genmars Business Platform — the web application.
 *
 * ── SAME ORIGIN AS THE API, AND THAT IS LOAD-BEARING ────────────────────────
 *
 * A subscriber signs in through Genmars and comes back to /auth/callback, which
 * sets a Django session cookie on business.genmars.co.ke. Django sets it with
 * NO `Domain` attribute, so the browser scopes it to that exact host — the same
 * property that keeps portal and ops sessions apart across the rest of the
 * company, and the reason SESSION_COOKIE_DOMAIN must never be set.
 *
 * Serve this app from any other origin and that cookie stops travelling. So in
 * production Caddy puts both on one host and splits by path:
 *
 *     /static/*                                   → disk
 *     /auth /org /brn /ctl /invt /sls /admin      → Django :8020
 *     everything else                             → this, :3030
 *
 * ── THE REWRITE BELOW IS NOT DEAD IN PRODUCTION, AND THAT IS A TRAP ─────────
 *
 * This said the rewrite was "for `next dev`, not for production — with Caddy in
 * front, those paths never reach Next at all, so the rewrite is dead in
 * production and correct anyway".
 *
 * ⚠ IT IS NOT DEAD. It is a SILENT FALLBACK, and it is why a missing Caddy
 *   prefix half-works instead of failing.
 *
 *   `/ntf/*` was added to this list and to deploy/business.caddy, and
 *   deploy/business.caddy is never copied onto the host automatically — so the
 *   live matcher still had the old list. Caddy therefore handed /ntf/ to Next,
 *   and THIS REWRITE PROXIED IT TO DJANGO. `/ntf/unread` and `/ntf/read`
 *   worked perfectly. Only the bare `/ntf/` broke, because Next strips the
 *   trailing slash with a 308 before any rewrite is applied, so the panel's
 *   own URL 308'd to `/ntf?limit=30` and then 301'd into nothing.
 *
 *   So the failure mode is one endpoint of three, which is the worst shape:
 *   enough works that a smoke test passes and the Caddy step looks done.
 *
 * ⚠ AND test_routing_lists.py CANNOT CATCH IT. That test compares Django's
 *   prefixes against this file and against deploy/business.caddy — all three
 *   in the REPOSITORY. It passed throughout, correctly, because the repository
 *   was right and the HOST was not. There is no test for the host; the only
 *   check is probing a bare prefix after a deploy and seeing whether Django or
 *   Next answers. Django gives DRF JSON with an `allow:` header and the
 *   `default-src 'none'` CSP; Next gives a 308 that strips the slash.
 *
 * Locally there is no Caddy, so the rewrite is also what lets the browser reach
 * Django on loopback — which is the job it was added for and still does.
 *
 * ⚠ REWRITES ARE RESOLVED AT BUILD TIME and written into routes-manifest.json.
 *   Setting API_ORIGIN in the runtime environment has NO effect on them. In a
 *   container it has to be a build arg. gen-portal shipped exactly this bug:
 *   the variable was present and correct in `docker compose exec web env` the
 *   whole time it was doing nothing, while every call returned ECONNREFUSED.
 *
 *   Server components do NOT go through the rewrite — src/lib/api.ts reads
 *   API_ORIGIN at request time, which is why the same variable is also set in
 *   the runtime environment. Both, deliberately, for two different consumers.
 */

/**
 * Every prefix Business_Platform/urls.py gives Django, and the third list
 * that has to agree with it — deploy/business.caddy carries the other two.
 *
 * `pay`, `sub` and `prc` were missing from all three. A server component
 * never notices, because src/lib/api.ts reaches API_ORIGIN directly; the
 * browser does, and the till pays by M-Pesa from the browser.
 */
const API_PREFIXES = [
  "auth",
  "org",
  "brn",
  "ctl",
  "invt",
  "sls",
  "pay",
  "sub",
  "prc",
  "ntf",
];

const nextConfig: NextConfig = {
  output: "standalone",

  /**
   * Trace from THIS directory rather than letting Next infer it. A stray
   * package-lock.json further up the tree makes Next pick that directory as
   * the workspace root, and with `output: standalone` the trace decides which
   * node_modules reach the runtime image. Getting it wrong produces an image
   * that builds cleanly and crashes on boot with a missing module.
   */
  outputFileTracingRoot: __dirname,

  reactStrictMode: true,
  poweredByHeader: false,
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },

  async rewrites() {
    const api = process.env.API_ORIGIN ?? "http://127.0.0.1:8020";
    return API_PREFIXES.map((prefix) => ({
      source: `/${prefix}/:path*`,
      destination: `${api}/${prefix}/:path*`,
    }));
  },
};

export default nextConfig;
