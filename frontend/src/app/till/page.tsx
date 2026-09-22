import { Till } from "./Till";

/**
 * The register.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE ONLY ROUTE IN THIS APPLICATION THAT DOES NOT ASK WHO THE SUBSCRIBER IS.
 *
 * Every other page calls /auth/me from the server and draws the Shell around
 * the answer. This one renders a client application that holds its own bearer
 * session, because the principal here is a cashier and a cashier has no
 * Genmars account and no session cookie — see the two-tier rule in CLAUDE.md,
 * decided 2026-09-21.
 *
 * So there is no Shell, no sidebar and no subscriber navigation: a till is a
 * terminal, not a dashboard, and offering a cashier a link to Reports would
 * only be offering them a refusal.
 * ══════════════════════════════════════════════════════════════════════════
 */

/*
 * ── DYNAMIC SO IT CAN CARRY A CSP NONCE ────────────────────────────────────
 *
 * This was `force-static`: the till holds its own session and fetches
 * everything client-side, so there was nothing per-request to render and
 * prerendering it once was free.
 *
 * A prerendered page is built once and served to everybody, which means it
 * cannot carry a per-request nonce — and Next's inline bootstrap scripts
 * would then be blocked by the policy in src/middleware.ts. The alternative
 * was 'unsafe-inline' for the whole application to spare one page a render.
 *
 * The cost is one server render per load of a page somebody opens at the
 * start of a shift.
 */
export const dynamic = "force-dynamic";

export const metadata = { title: "Till" };

export default function TillPage() {
  return <Till />;
}
