"use client";

/**
 * Money at the till.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE SERVER PRICES THE SALE. THIS IS A PREVIEW AND NOTHING MORE.
 *
 * sales/services.py computes every figure on a sale in Decimal, rounds half
 * up, and writes the result. What is below runs in JavaScript, where 0.1 +
 * 0.2 is 0.30000000000000004 — so it exists to show a cashier a running total
 * while they build a basket, and the number that is charged is the one that
 * comes back from /sls/sales/checkout/.
 *
 * Any screen that treats a figure from this file as the amount taken is
 * wrong, and any disagreement between the two is the preview being wrong.
 * Whole-cent arithmetic below keeps the disagreement at zero in practice; the
 * rule stands regardless of whether it ever fires.
 * ══════════════════════════════════════════════════════════════════════════
 */

/** Cents, as an integer. Rounds half away from zero, as the server does. */
export function cents(value: string | number): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.round(Math.abs(n) * 100) * Math.sign(n || 1);
}

export function shillings(c: number): string {
  const sign = c < 0 ? "-" : "";
  const abs = Math.abs(c);
  return `${sign}${Math.floor(abs / 100).toLocaleString("en-KE")}.${String(
    abs % 100,
  ).padStart(2, "0")}`;
}

export type Basket = {
  productId: number;
  name: string;
  sku: string;
  unitCents: number;
  quantity: number;
  /** Percent, as the rule states it. 0 when the product has no rule. */
  taxRate: number;
  taxInclusive: boolean;
};

/**
 * What the basket comes to — in the SAME THREE FIGURES the server reports.
 *
 * ── `subtotal` IS GROSS WHEN THE RULE IS INCLUSIVE ─────────────────────────
 *
 * That is what sales/services.py writes: it sums the line amounts as charged,
 * so an inclusive rule leaves the tax sitting inside the subtotal and `total`
 * equals it. Only an exclusive rule adds anything on.
 *
 * The preview used to report the NET as its subtotal, which agreed with the
 * server on the total and disagreed on the two figures above it — so the
 * basket and the receipt printed different numbers for the same sale. A till
 * whose running total is not the number on the slip is a till nobody trusts.
 *
 * Mixed baskets are ordinary — zero-rated bread beside standard-rated soap —
 * so each line is worked out on its own rule and only then summed.
 */
export function total(lines: Basket[]): {
  subtotal: number;
  tax: number;
  /** How much of `tax` is already inside `subtotal`. Labels the row. */
  taxIncluded: number;
  total: number;
} {
  let subtotal = 0;
  let tax = 0;
  let taxIncluded = 0;
  let added = 0;

  for (const line of lines) {
    const gross = line.unitCents * line.quantity;
    subtotal += gross;

    if (line.taxRate <= 0) continue;

    if (line.taxInclusive) {
      const net = Math.round(gross / (1 + line.taxRate / 100));
      const inside = gross - net;
      tax += inside;
      taxIncluded += inside;
    } else {
      const on = Math.round((gross * line.taxRate) / 100);
      tax += on;
      added += on;
    }
  }

  return { subtotal, tax, taxIncluded, total: subtotal + added };
}
