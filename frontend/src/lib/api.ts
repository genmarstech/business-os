/**
 * Talking to Django from a server component.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THIS FILE IS SERVER-ONLY. It reads the incoming request's cookies and
 * forwards them, which is exactly what must never happen in a browser bundle.
 * `import "server-only"` makes a mistaken client import a build error rather
 * than a runtime surprise.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── TWO PATHS TO THE SAME DJANGO, FOR TWO DIFFERENT CALLERS ────────────────
 *
 *   server components  →  API_ORIGIN, read HERE at request time
 *   the browser        →  same-origin, split by Caddy (or by the dev rewrite)
 *
 * They are separate on purpose and the difference has bitten this company
 * before: next.config.ts resolves its rewrites at BUILD time, so API_ORIGIN
 * has to be both a build arg and a runtime variable. See the banner there.
 *
 * ── WHY THE COOKIE IS FORWARDED BY HAND ────────────────────────────────────
 *
 * A server component's `fetch` carries no cookies of its own — it is the
 * server calling another server, not the browser. Without this the API sees an
 * anonymous caller and answers 403, which reads like a broken session and is
 * not one.
 *
 * Only the two cookies Django issues are forwarded, by name. Passing the
 * browser's whole jar would send anything else on the host — a third-party
 * analytics id, a preference cookie — to an origin that has no business
 * receiving it, and would do so invisibly.
 */

import "server-only";

import { cookies, headers } from "next/headers";

const ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8020";

/** Django's defaults. Named here so a settings change breaks loudly. */
const FORWARDED = ["sessionid", "csrftoken"];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  /**
   * ── 404 FREQUENTLY MEANS "NOT YOURS" ───────────────────────────────────
   * The API answers 404 rather than 403 for a record outside the caller's
   * tenant, deliberately: a 403 confirms the row exists, which is an
   * enumeration oracle. A screen must therefore never offer "retry" or
   * "request access" on one — it is a dead end by design.
   */
  get isMissing() {
    return this.status === 404;
  }

  /**
   * A real permission refusal, about the caller's own authority. Safe to
   * explain plainly, unlike the above.
   */
  get isForbidden() {
    return this.status === 403;
  }
}

/**
 * Headers that must survive the hop from this server to Django.
 *
 * ── THE HOST HEADER IS NOT OPTIONAL ────────────────────────────────────────
 *
 * Server-side fetches go to API_ORIGIN, which in production is
 * `http://api:8020` — a compose service name. Django therefore sees
 * `Host: api:8020`, which is not in ALLOWED_HOSTS, and answers **400 Bad
 * Request** to everything.
 *
 * It does not show up in development, because ALLOWED_HOSTS contains
 * 127.0.0.1 when DEBUG is on and that is exactly what the dev fetch sends. So
 * the whole app works locally and returns 500 on the server, with a Django log
 * line about DisallowedHost that nothing in the frontend mentions.
 *
 * The fix is to forward the host the BROWSER asked for, not to widen
 * ALLOWED_HOSTS to include an internal service name. Two reasons:
 *
 *   · ALLOWED_HOSTS stays exactly the public name, which is what it is for.
 *   · Any absolute URL Django builds — a redirect, a link in an email — comes
 *     out as business.genmars.co.ke rather than `api:8020`.
 *
 * Forwarding a client-supplied Host is safe here precisely because Django
 * still validates it: Caddy only routes business.genmars.co.ke to this app,
 * and anything else is refused at the other end rather than trusted.
 */
async function forwarded(): Promise<Record<string, string>> {
  const incoming = await headers();
  const out: Record<string, string> = {};

  const host = incoming.get("host");
  if (host) out.host = host;

  // Django reads this to know the original request was HTTPS. Without it a
  // CSRF origin check on an unsafe method compares against http:// and fails.
  const proto = incoming.get("x-forwarded-proto");
  if (proto) out["x-forwarded-proto"] = proto;

  const cookie = await cookieHeader();
  if (cookie) out.cookie = cookie;

  return out;
}

async function cookieHeader(): Promise<string> {
  const jar = await cookies();
  return FORWARDED.map((name) => jar.get(name))
    .filter((c): c is { name: string; value: string } => Boolean(c))
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");
}

/**
 * GET `path` as the signed-in caller.
 *
 * `cache: "no-store"` is not a performance oversight. Every response here is
 * scoped to one principal, and a cached one is one tenant's data served to
 * another — the single worst bug this application could have.
 */
export async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${ORIGIN}${path}`, {
    headers: await forwarded(),
    cache: "no-store",
  });

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw new ApiError(
      `GET ${path} → ${response.status}`,
      response.status,
      body,
    );
  }

  return (await response.json()) as T;
}

/**
 * Like `get`, but an unauthenticated or out-of-scope answer returns null
 * instead of throwing.
 *
 * For the places where "not signed in" is an ordinary state rather than an
 * error — the root route has to decide between a landing page and a dashboard,
 * and a thrown error there would render an error screen to somebody who has
 * simply not signed in yet.
 */
export async function getOrNull<T>(path: string): Promise<T | null> {
  try {
    return await get<T>(path);
  } catch (error) {
    if (
      error instanceof ApiError &&
      [401, 403, 404].includes(error.status)
    ) {
      return null;
    }
    throw error;
  }
}
