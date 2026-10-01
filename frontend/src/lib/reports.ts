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

/* ════════════════════════════════════════════════════════════════════════
 * BUYING — /prc/reports/*
 *
 * The same window machinery, a different ledger. Kept in this file because
 * it is "the one place that knows the shapes of the reporting endpoints",
 * and two files would mean two definitions of `Window` drifting apart.
 *
 * ── ORDERED AND RECEIVED ARE NOT THE SAME NUMBER ───────────────────────
 * `ordered_value` is what the shop has committed to; `received_value` is
 * what has actually arrived and therefore what it has spent. The server
 * keeps them apart deliberately (see procurement/reports.py) and so does
 * every screen that draws them — presenting either one as "spend" on its
 * own is how a quarter with stock in transit reads as overspent.
 * ════════════════════════════════════════════════════════════════════════ */

export type BuyingOverview = {
  from: string;
  to: string;
  orders_raised: number;
  ordered_value: string;
  orders_cancelled: number;
  cancelled_value: string;
  deliveries: number;
  received_value: string;
  lines_received: number;
  suppliers_used: number;
};

export type SupplierSpendRow = {
  supplier: number;
  supplier_name: string;
  received_value: string;
  orders: number;
  ordered_value: string;
};

export type PurchasedProductRow = {
  product: number | null;
  product_name: string;
  sku: string;
  quantity: string;
  value: string;
};

export type OutstandingOrder = {
  order: number;
  number: number;
  status: string;
  status_label: string;
  supplier: number;
  supplier_name: string;
  branch: number;
  branch_name: string;
  /** null means unscheduled, which is NOT the same as overdue. */
  expected_at: string | null;
  overdue: boolean;
  days_late: number;
  owed: string;
};

export type Outstanding = {
  as_of: string;
  committed: string;
  overdue: string;
  orders: OutstandingOrder[];
};

export type ReliabilityRow = {
  supplier: number;
  supplier_name: string;
  orders: number;
  on_time: number;
  late: number;
  /** Orders with no expected date — they have no verdict, not a bad one. */
  unscheduled: number;
  judged: number;
  /** null when nothing could be judged. Not zero: zero means "never on time". */
  on_time_rate: number | null;
  average_days_late: number | null;
};

/**
 * Every buying report for one window, in one pass.
 *
 * `outstanding` takes no window on purpose — it is a position as of now, not
 * a period. Asking "what was outstanding during September" has no single
 * answer, and a date filter on it would quietly produce a number that looks
 * like one.
 */
export async function buying(window: Window) {
  const q = query(window);
  const branch = window.branch ? `?branch=${window.branch}` : "";
  const [overview, suppliers, products, owed, reliability] = await Promise.all([
    getOrNull<BuyingOverview>(`/prc/reports/overview/${q}`),
    getOrNull<{ suppliers: SupplierSpendRow[] }>(`/prc/reports/by-supplier/${q}`),
    getOrNull<{ products: PurchasedProductRow[] }>(`/prc/reports/by-product/${q}`),
    getOrNull<Outstanding>(`/prc/reports/outstanding/${branch}`),
    getOrNull<{ suppliers: ReliabilityRow[] }>(`/prc/reports/reliability/${q}`),
  ]);

  return {
    overview,
    suppliers: suppliers?.suppliers ?? [],
    products: products?.products ?? [],
    outstanding: owed,
    reliability: reliability?.suppliers ?? [],
  };
}
