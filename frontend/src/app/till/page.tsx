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

export const dynamic = "force-static";

export const metadata = { title: "Till" };

export default function TillPage() {
  return <Till />;
}
