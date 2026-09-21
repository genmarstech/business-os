"use client";

/**
 * The till's own session, held in the browser.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * WHY THIS IS A BEARER TOKEN IN THE BROWSER AND NOT A COOKIE ON THE SERVER.
 *
 * Every other screen in this application is server-rendered and reads a
 * Django session cookie the Next server forwards. The till deliberately is
 * not, and the reason is in the banner on StaffSession: a till is not a
 * browser tab. It is a fixed terminal that stays signed in for a whole shift,
 * is meant to survive becoming an offline-capable client (blueprint §11), and
 * holds its credential EXPLICITLY rather than having one set on it invisibly.
 *
 * It also has to be fast. Blueprint §6 asks for a register that keeps up with
 * a queue, and a server round trip for every keystroke of a product search is
 * the opposite of that. The catalogue is fetched once per shift and searched
 * in memory.
 *
 * What that costs: the token is reachable by script on this origin, so an
 * injection here is a stolen shift. Against it — the token is scoped to one
 * tenant and expires with the shift, a manager can end every session of a
 * credential at once from /staff, and this application ships no `innerHTML`
 * and no third-party script.
 *
 * ⚠ THE TOKEN MUST NEVER BE PUT IN A URL. Not as a query parameter, not in a
 *   redirect, not in a printed receipt link. URLs reach logs, proxies and the
 *   browser's own history; a header does not.
 * ══════════════════════════════════════════════════════════════════════════
 */

const KEY = "till.session";
const ORG = "till.organisation";

export type TillSession = {
  token: string;
  expires_at: string;
  must_change_password: boolean;
  /**
   * `id` is the cashier's own OrganizationStaff row.
   *
   * The till needs it to open a shift and to name itself on a sale, and there
   * is no other way to learn it: /org/staff/ is held at staff.manage, which no
   * cashier holds. It confers nothing — checkout pins the cashier to the
   * authenticated principal whatever is sent.
   */
  staff: { id: number; name: string; username: string };
  organisation: { id: number; name: string };
};

function safe<T>(read: () => T, fallback: T): T {
  // A till may be a kiosk browser with storage disabled, or in a private
  // window. Every read and write is wrapped because the accessor itself
  // throws in those, and a thrown accessor is a white screen at a counter.
  try {
    return read();
  } catch {
    return fallback;
  }
}

export function load(): TillSession | null {
  return safe(() => {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const session = JSON.parse(raw) as TillSession;
    if (!session?.token) return null;
    // Expiry is checked here as well as by the server, so a till that has
    // been closed over a weekend shows its sign-in screen rather than a row
    // of failed requests.
    if (new Date(session.expires_at).getTime() <= Date.now()) {
      window.localStorage.removeItem(KEY);
      return null;
    }
    return session;
  }, null);
}

export function save(session: TillSession): void {
  safe(() => {
    window.localStorage.setItem(KEY, JSON.stringify(session));
    // Remembered separately and on purpose: after signing out, the next
    // person should not have to be told which business this terminal belongs
    // to. It is not a secret — a shop's own number tells nobody anything
    // without a username and password for that shop.
    window.localStorage.setItem(ORG, String(session.organisation.id));
    return null;
  }, null);
}

export function forget(): void {
  safe(() => {
    window.localStorage.removeItem(KEY);
    return null;
  }, null);
}

export function rememberedOrganisation(): string {
  return safe(() => window.localStorage.getItem(ORG) ?? "", "");
}

/**
 * Call the API as the till.
 *
 * Same origin — Caddy routes /auth, /sls and the rest to Django and
 * everything else to this application, so there is no CORS and no proxy hop.
 * A 401 or 403 clears the session rather than retrying: a revoked credential
 * must put the sign-in screen up at once, which is the whole point of
 * revocation taking effect immediately.
 */
export class TillError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = "TillError";
  }
}

export async function call<T>(
  path: string,
  options: { method?: string; body?: unknown; token?: string } = {},
): Promise<T> {
  const token = options.token ?? load()?.token;

  const response = await fetch(path, {
    method: options.method ?? "GET",
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { "content-type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: "no-store",
  });

  if (response.status === 401 || response.status === 403) {
    if (token) forget();
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw new TillError(`${path} → ${response.status}`, response.status, body);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** The first sentence of a DRF error, in words somebody at a counter can use. */
export function readError(error: unknown, fallback: string): string {
  if (!(error instanceof TillError)) return fallback;
  const body = error.body;
  if (typeof body === "string") return body;
  if (body && typeof body === "object") {
    for (const value of Object.values(body as Record<string, unknown>)) {
      const text = Array.isArray(value) ? value[0] : value;
      if (typeof text === "string" && text.trim()) return text;
    }
  }
  return fallback;
}
