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
 * ── THE HOST HEADER IS NOT HERE, AND CANNOT BE ─────────────────────────────
 *
 * Server-side fetches go to API_ORIGIN, which in production is
 * `http://api:8020` — a compose service name — so Django sees `Host: api:8020`
 * and must accept it. The obvious fix from this side is to forward the host
 * the browser asked for. It does not work: Node's fetch treats `Host` as a
 * forbidden header, strips it, and computes the value from the URL. Setting it
 * is silently ignored, which is worse than an error.
 *
 * So the service name is allowed on the Django side instead — see the banner
 * on ALLOWED_HOSTS in Business_Platform/settings.py, which explains why that
 * costs nothing. Do not try to set `host` here again.
 *
 * ⚠ THE FAILURE IS INVISIBLE IN DEVELOPMENT. DEBUG puts 127.0.0.1 in
 *   ALLOWED_HOSTS and that is exactly what the dev fetch sends, so the whole
 *   application works locally and every server-rendered page is a 500 the
 *   moment it is containerised.
 */
async function forwarded(): Promise<Record<string, string>> {
  const incoming = await headers();
  const out: Record<string, string> = {};

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

/**
 * POST `path` as the signed-in caller.
 *
 * ── THE CSRF TOKEN IS ECHOED FROM THE COOKIE ───────────────────────────────
 *
 * Django's check compares a header against the cookie: holding the cookie is
 * what proves the request came from a page on this origin rather than from
 * somebody else's.
 *
 * ⚠ THE TOKEN COMES FROM /auth/callback, NOT FROM /auth/me.
 *
 * /auth/me carries @ensure_csrf_cookie and looks like the source, and that is
 * what this comment used to say. It was wrong, and it made every subscriber
 * write impossible: me() calls /auth/me from THIS server, so the Set-Cookie
 * comes back on a fetch response that is read for its body and discarded. The
 * browser never saw it, the jar below was always empty, and Django refused
 * every POST with "CSRF cookie not set".
 *
 * The sign-on callback is the one Django response the browser itself
 * receives, so that is where the cookie is now minted — see the banner on
 * SignOnCallbackView. Anything else that wants a subscriber to be able to
 * write has the same problem to solve and the same answer.
 *
 * Read FRESH on every call rather than captured once. Django rotates the
 * token when a session starts, so one read at page load goes stale the moment
 * somebody signs in and every subsequent write fails with a refusal that
 * looks like a permission problem.
 */
export async function post<T>(path: string, body: unknown): Promise<T> {
  return write<T>("POST", path, body);
}

/**
 * PATCH `path` — a partial update.
 *
 * Shares every line of `post` below, and must: the CSRF token, the forwarded
 * cookie and the referer are not per-verb concerns, and a second copy is a
 * second place for one of them to be forgotten.
 */
export async function patch<T>(path: string, body: unknown): Promise<T> {
  return write<T>("PATCH", path, body);
}

async function write<T>(
  method: "POST" | "PATCH",
  path: string,
  body: unknown,
): Promise<T> {
  const jar = await cookies();
  const token = jar.get("csrftoken")?.value ?? "";

  /*
   * Without the token Django answers 403 "CSRF failed: CSRF cookie not set",
   * which a form would render verbatim at somebody who has done nothing wrong
   * and can do nothing about it. Say the one thing that fixes it instead.
   *
   * The only way to be here is a session that began before the callback
   * started minting the cookie, or a jar somebody has cleared by hand.
   * Signing in again mints one.
   */
  if (!token) {
    throw new ApiError(`${method} ${path} → no CSRF token`, 403, {
      detail: "Your sign-in needs refreshing before you can save changes. Sign in again.",
    });
  }

  const response = await fetch(`${ORIGIN}${path}`, {
    method,
    headers: {
      ...(await forwarded()),
      "content-type": "application/json",
      "x-csrftoken": token,
      // Django checks this against CSRF_TRUSTED_ORIGINS on unsafe methods.
      // Without it a request over HTTPS is refused for a reason that names
      // neither the header nor the setting.
      ...(await refererHeader()),
    },
    body: JSON.stringify(body),
    cache: "no-store",
  });

  if (!response.ok) {
    let parsed: unknown;
    try {
      parsed = await response.json();
    } catch {
      parsed = undefined;
    }
    throw new ApiError(
      `${method} ${path} → ${response.status}`,
      response.status,
      parsed,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function refererHeader(): Promise<Record<string, string>> {
  const incoming = await headers();
  const host = incoming.get("host");
  const proto = incoming.get("x-forwarded-proto") ?? "http";
  return host ? { referer: `${proto}://${host}/` } : {};
}

/**
 * Turn an ApiError into the field errors a form can show.
 *
 * DRF answers a failed create with `{"field": ["message"], ...}` and a refused
 * one with `{"detail": "…"}`. Both are flattened to one shape so a form has a
 * single thing to render, and anything unrecognised becomes a general message
 * rather than an empty form that silently did nothing.
 */
export type FormErrors = { field: Record<string, string>; general: string[] };

export function asFormErrors(error: unknown): FormErrors {
  const out: FormErrors = { field: {}, general: [] };

  if (!(error instanceof ApiError)) {
    out.general.push("Something went wrong. Try again.");
    return out;
  }

  const body = error.body;
  if (body && typeof body === "object") {
    for (const [key, value] of Object.entries(body as Record<string, unknown>)) {
      const text = Array.isArray(value) ? value.join(" ") : String(value);
      if (key === "detail" || key === "non_field_errors") {
        out.general.push(text);
      } else {
        out.field[key] = text;
      }
    }
  }

  if (!out.general.length && !Object.keys(out.field).length) {
    out.general.push(
      error.isForbidden
        ? "You do not have permission to do that."
        : "That did not work. Check the details and try again.",
    );
  }

  return out;
}
