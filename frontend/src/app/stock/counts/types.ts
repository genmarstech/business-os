/**
 * The shapes the counting endpoints return.
 *
 * Mirrors `backend/inventory/serializers.py`. Quantities are strings for the
 * reason lib/money.ts spells out, and nothing in this section parses one —
 * except `variance`, which is compared against zero to decide a colour and
 * never shown as a computed figure. The server sends the number; the screen
 * only asks which side of nothing it is on.
 */

export type CountStatus = "open" | "closed" | "abandoned";

export type CountLine = {
  id: number;
  inventory: number;
  product_name: string;
  product_sku: string;
  expected_quantity: string;
  counted_quantity: string;
  variance: string;
  counted_by_name: string;
  counted_at: string;
  note: string;
  movement: number | null;
};

export type CountSummary = {
  counted: number;
  agreed: number;
  short: number;
  over: number;
  units_short: string;
  units_over: string;
};

export type Count = {
  id: number;
  number: number;
  organization: number;
  branch: number;
  branch_name: string;
  status: CountStatus;
  status_label: string;
  /**
   * One name out of two columns — a count can be signed by an employee at a
   * till or by a subscriber in the office, and the screen does not care
   * which. See StockCount.opened_by_name.
   */
  opened_by_name: string;
  opened_at: string;
  closed_by_name: string;
  closed_at: string | null;
  note: string;
  lines: CountLine[];
  summary: CountSummary;
};

export type Branch = { id: number; branch_name: string; is_active: boolean };

export type Stock = {
  id: number;
  branch: number;
  product_name: string;
  product_sku: string;
};
