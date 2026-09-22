import "server-only";

import { getOrNull } from "./api";

/**
 * The reporting endpoints, and the one place that knows their shapes.
 *
 * ── EVERY FIGURE ARRIVES AS A STRING, AND STAYS ONE UNTIL IT IS DRAWN ──────
 *
 * sales/views.py renders Decimals as strings on purpose: DRF's encoder does
 * float(obj), and 19.99 leaves as 19.989999999999998. Money that has been
 * through a float is money that no longer adds up, and a dashboard whose
 * total disagrees with the sum of its rows by a cent is one nobody trusts
 * again.
 *
 * So nothing here parses. The strings are formatted for display and never
 * summed on this side — if a total is needed, the server already computed it.
 */

/**
 * ── THE PERIOD IS NAMED, NOT DATED ─────────────────────────────────────────
 *
 * `range=today|week|month|year` and the server works out what that means in
 * the shop's clock. Computing the dates here got it wrong and got it wrong
 * silently: this server runs on UTC in a container, a shop in Nairobi is
 * three hours ahead, and for three hours every night the dashboard asked for
 * yesterday and reported a day of no trade.
 *
 * `from`/`to` remain for a custom window somebody types, and are passed
 * through untouched — sales/reports.py reads them as whole local days.
 */
export type Window = {
  range?: string;
  from?: string;
  to?: string;
  branch?: number;
};

export type Overview = {
  from: string;
  to: string;
  revenue: string;
  tax_collected: string;
  discounts_given: string;
  transactions: number;
  average_basket: string;
  items_sold: string;
  cost_of_sales: string;
  gross_profit: string;
  refunded: string;
  refunds: number;
};

export type BranchRow = {
  branch: number;
  branch_name: string;
  revenue: string;
  transactions: number;
};

export type ProductRow = {
  product: number;
  product_name: string;
  sku: string;
  quantity: string;
  revenue: string;
  gross_profit: string;
};

export type CashierRow = {
  cashier: number;
  cashier_name: string;
  revenue: string;
  transactions: number;
};

export type MethodRow = {
  method: string;
  method_label: string;
  amount: string;
  count: number;
};

export type RegisterRow = {
  shift: number;
  register_name: string;
  branch_name: string;
  operator_name: string;
  opened_at: string;
  opening_cash: string;
  cash_taken: string;
  change_given: string;
  expected_cash: string;
  revenue: string;
  transactions: number;
};

export type StockAlert = {
  product_name: string;
  sku: string;
  branch_name: string;
  quantity: string;
  reorder_level: string;
};

function query(window: Window): string {
  const params = new URLSearchParams();
  if (window.range) params.set("range", window.range);
  if (window.from) params.set("from", window.from);
  if (window.to) params.set("to", window.to);
  if (window.branch) params.set("branch", String(window.branch));
  const q = params.toString();
  return q ? `?${q}` : "";
}

/**
 * Every report for one window, in one pass.
 *
 * `getOrNull` rather than `get`: a caller who may read branch figures but not
 * organisation ones is refused some of these, and that is an ordinary answer
 * for their role rather than an error. The screen omits what it did not get
 * instead of failing whole — see identity/access.py, which is emphatic that
 * reports.branch and reports.organisation are different permissions.
 */
export async function dashboard(window: Window) {
  const q = query(window);
  const [overview, branches, products, cashiers, methods, registers, alerts] =
    await Promise.all([
      getOrNull<Overview>(`/sls/reports/overview/${q}`),
      getOrNull<{ branches: BranchRow[] }>(`/sls/reports/by-branch/${q}`),
      getOrNull<{ products: ProductRow[] }>(`/sls/reports/by-product/${q}`),
      getOrNull<{ cashiers: CashierRow[] }>(`/sls/reports/by-cashier/${q}`),
      getOrNull<{ methods: MethodRow[] }>(
        `/sls/reports/by-payment-method/${q}`,
      ),
      getOrNull<{ registers: RegisterRow[] }>(`/sls/reports/register-status/${q}`),
      getOrNull<{ alerts: StockAlert[] }>(`/sls/reports/stock-alerts/${q}`),
    ]);

  return {
    overview,
    branches: branches?.branches ?? [],
    products: products?.products ?? [],
    cashiers: cashiers?.cashiers ?? [],
    methods: methods?.methods ?? [],
    registers: registers?.registers ?? [],
    alerts: alerts?.alerts ?? [],
  };
}

export { amount, ksh } from "./money";
