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
 * ── THE REWRITE BELOW IS FOR `next dev`, NOT FOR PRODUCTION ─────────────────
 *
 * With Caddy in front, those paths never reach Next at all, so the rewrite is
 * dead in production and correct anyway. Locally there is no Caddy, so it is
 * what lets the browser reach Django on loopback.
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

const API_PREFIXES = ["auth", "org", "brn", "ctl", "invt", "sls"];

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
