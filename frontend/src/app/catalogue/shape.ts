/**
 * What a catalogue screen passes around.
 *
 * Client components read this, so nothing here fetches or touches a cookie —
 * see the note on the staff screens' shape.ts for why that split exists.
 */

export type Category = {
  id: number;
  name: string;
  description?: string;
  is_active: boolean;
};

export type TaxRule = {
  id: number;
  name: string;
  rate: string;
  is_inclusive: boolean;
  is_default: boolean;
  is_active: boolean;
};

export type Product = {
  id: number;
  name: string;
  description?: string;
  sku: string;
  barcode?: string;
  cost_price: string;
  selling_price: string;
  is_active: boolean;
  tax_rule: number | null;
  category: Category | number | null;
};

export function categoryOf(product: Product): number | null {
  const c = product.category;
  if (c === null || c === undefined) return null;
  return typeof c === "number" ? c : c.id;
}

/**
 * What the shop keeps of a sale at this price, before tax.
 *
 * ── THE SHELF PRICE INCLUDES THE TAX WHEN THE RULE SAYS SO ─────────────────
 * A Kenyan shelf price normally does: 116 on the label, 16 of it VAT. Taking
 * the margin off the gross figure would overstate every product on the screen
 * by the tax rate, which is exactly the kind of number somebody prices a whole
 * shop against.
 */
export function margin(
  product: Product,
  rule: TaxRule | undefined,
): { net: number; profit: number; percent: number } | null {
  const gross = Number(product.selling_price);
  const cost = Number(product.cost_price);
  if (!Number.isFinite(gross) || !Number.isFinite(cost) || gross <= 0) {
    return null;
  }

  const rate = rule && rule.is_active ? Number(rule.rate) : 0;
  const net =
    rule?.is_inclusive && rate > 0 ? gross / (1 + rate / 100) : gross;

  const profit = net - cost;
  return { net, profit, percent: net > 0 ? (profit / net) * 100 : 0 };
}

export function money(value: number): string {
  return value.toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}
