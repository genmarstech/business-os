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
  /** Revenue with the tax taken out — the base the profit is figured on. */
  net_revenue: string;
  /**
   * Why this is here: ranked on turnover alone, the comparison puts a busy
   * low-margin branch above a quieter one that actually makes money, which is
   * the opposite of the decision somebody opens it to make.
   */
  gross_profit: string;
  /** Beside revenue, never netted off it. A returns problem has to show. */
  refunded: string;
  refunds: number;
};

/**
 * A branch the caller may pick, whether or not it traded in the window.
 *
 * Taken from `/brn/branches/` rather than from the comparison, which is built
 * from completed sales — a branch with a till open and nothing sold yet would
 * be unselectable, and that is the branch somebody is most likely checking
 * on. The endpoint is tenant-scoped AND branch-confined, so a branch manager
 * is offered their own branches and never the organisation's list.
 */
export type BranchOption = {
  id: number;
  branch_name: string;
  is_active: boolean;
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

/**
 * Every term of the expected figure, because the screen shows all of them.
 *
 * ── THE COLUMNS HAVE TO RECONCILE TO THE ONE BESIDE THEM ───────────────────
 *
 * `cash_taken` is what crossed the counter and `change_given` is what went
 * back, so opening + taken − change + paid in − paid out − returned is
 * `expected_cash` exactly. A manager looking at a drawer that does not add up
 * adds these up; a screen where they do not reach the total is a screen that
 * sends them to a spreadsheet instead.
 *
 * It did not reconcile until 2026-10-10, and the figure was wrong rather than
 * just unexplained — see the banner in branches/services.py.
 */
type DrawerTerms = {
  opening_cash: string;
  cash_taken: string;
  change_given: string;
  paid_in: string;
  paid_out: string;
  refunded_cash: string;
  expected_cash: string;
};

export type RegisterRow = DrawerTerms & {
  shift: number;
  register_name: string;
  branch_name: string;
  operator_name: string;
  opened_at: string;
  revenue: string;
  transactions: number;
};

/** A drawer that has already been counted. */
export type DrawerRow = DrawerTerms & {
  shift: number;
  register_name: string;
  branch_name: string;
  /**
   * ⚠ WHO WAS ON THE TILL, NOT WHO COUNTED IT. Nothing records the person
   *   who closed a shift, and a column headed "counted by" over this name
   *   would be naming the one person a count is meant to be independent of.
   */
  operator_name: string;
  opened_at: string;
  closed_at: string;
  counted_cash: string | null;
  /** Positive is over, negative is short. Null when nobody ever counted. */
  variance: string | null;
  /**
   * A shift closed before the count existed — CLOSED with no `closing_cash`.
   * Rendering it as balanced would be the most flattering possible lie about
   * a drawer nobody looked in.
   */
  uncounted: boolean;
  note: string;
};

export type DrawerSummary = {
  closed: number;
  counted: number;
  balanced: number;
  short: number;
  over: number;
  /**
   * A NET. One till 500 short and another 500 over nets to zero, and that is
   * two problems rather than none — which is why `short` and `over` are shown
   * beside it and never folded into it.
   */
  net_variance: string;
  worst_short: string;
  /** More drawers were counted in this window than were read. */
  truncated: boolean;
  closed_in_window: number;
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
  const [
    overview,
    branches,
    products,
    cashiers,
    methods,
    registers,
    alerts,
    drawers,
    branchList,
  ] = await Promise.all([
      getOrNull<Overview>(`/sls/reports/overview/${q}`),
      getOrNull<{ branches: BranchRow[] }>(`/sls/reports/by-branch/${q}`),
      getOrNull<{ products: ProductRow[] }>(`/sls/reports/by-product/${q}`),
      getOrNull<{ cashiers: CashierRow[] }>(`/sls/reports/by-cashier/${q}`),
      getOrNull<{ methods: MethodRow[] }>(
        `/sls/reports/by-payment-method/${q}`,
      ),
      getOrNull<{ registers: RegisterRow[] }>(`/sls/reports/register-status/${q}`),
      getOrNull<{ alerts: StockAlert[] }>(`/sls/reports/stock-alerts/${q}`),
      getOrNull<{ shifts: DrawerRow[]; summary: DrawerSummary }>(
        `/sls/reports/drawers/${q}`,
      ),
      /*
       * Deliberately NOT narrowed by `window.branch`. This is the list of
       * branches to choose from, so filtering it by the current choice would
       * leave the picker holding the one branch already selected and no way
       * back out of it.
       */
      getOrNull<BranchOption[] | { results?: BranchOption[] }>("/brn/branch/"),
    ]);

  return {
    overview,
    branches: branches?.branches ?? [],
    products: products?.products ?? [],
    cashiers: cashiers?.cashiers ?? [],
    methods: methods?.methods ?? [],
    registers: registers?.registers ?? [],
    alerts: alerts?.alerts ?? [],
    /*
     * `drawers` stays null when the caller was refused, and that is NOT the
     * same as a window in which nothing was counted. The panel is omitted
     * for the first and says so for the second — "no drawers were counted"
     * printed at somebody who simply may not see them would be false.
     */
    drawers: drawers ?? null,
    /*
     * ── `/brn/branch/`, SINGULAR, AND THAT IS NOT A DETAIL ─────────────────
     *
     * This read `/brn/branches/` and 404ed. `getOrNull` turns a 404 into
     * null — correct, because a caller refused a report is an ordinary case
     * here — and `?? []` then turned that into no branches, so the picker
     * rendered nothing at all. A green build, a green suite and a feature
     * that silently did not exist.
     *
     * The rest of the application has always called it `/brn/branch/`; five
     * other screens say so.
     *
     * Both page shapes are accepted because the rest of the application
     * does, and inactive branches are dropped for the same reason — a closed
     * branch is not somewhere to report on.
     */
    branchOptions: (Array.isArray(branchList)
      ? branchList
      : (branchList?.results ?? [])
    ).filter((branch) => branch.is_active),
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
