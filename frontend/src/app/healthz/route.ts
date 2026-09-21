/**
 * Liveness for the web container.
 *
 * ── DELIBERATELY NOT `/` ───────────────────────────────────────────────────
 * The root route renders differently for a signed-in caller and a stranger,
 * and it calls Django to find out which. A health check that exercises a
 * session path fails when the API is slow, the database is blipping, or a
 * cookie is malformed — none of which mean this process is dead.
 *
 * Django's own /healthz carries the same note for the same reason, and the
 * first version of business.caddy pointed at /auth/me, which returns 403 to
 * an anonymous caller: Caddy marked the app permanently unhealthy and served
 * 502 to everybody. Anything requiring a credential cannot be a health check.
 *
 * Note the path: Caddy routes /healthz to DJANGO, not here, so this answers
 * only the container's own HEALTHCHECK on loopback. Both exist; they check
 * different processes.
 */
export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok" });
}
