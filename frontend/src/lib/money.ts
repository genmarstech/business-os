/**
 * Formatting money and quantities for a screen.
 *
 * ── SEPARATE FROM reports.ts BECAUSE CLIENT COMPONENTS NEED IT ─────────────
 *
 * reports.ts is `server-only` — it forwards the caller's cookies — so a client
 * component importing `ksh` from it is a build error, and rightly: that is how
 * a session cookie ends up in a browser bundle. These two functions touch
 * nothing but strings, so they are safe to ship.
 *
 * ── AND THEY NEVER PARSE ──────────────────────────────────────────────────
 *
 * Every figure arrives from the server as a string on purpose: DRF's encoder
 * does float(obj), and 19.99 leaves as 19.989999999999998. Money that has been
 * through a float is money that no longer adds up. These format for display
 * and hand the string back — nothing here turns one into a number and nothing
 * here sums anything.
 */

/** A money string, grouped for reading. Never parsed back into a number. */
export function ksh(value: string | null | undefined): string {
  if (!value) return "0.00";
  const negative = value.startsWith("-");
  const [whole = "0", fraction = "00"] = value.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}.${fraction.padEnd(2, "0").slice(0, 2)}`;
}

/** A quantity string, without money's insistence on two decimals. */
export function amount(value: string | null | undefined): string {
  if (!value) return "0";
  const trimmed = value.replace(/\.00$/, "");
  return trimmed.replace(/\B(?=(\d{3})+(?!\d))(?=[^.]*$)/g, ",");
}
