import { headers } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import {
  amount,
  dashboard,
  ksh,
  type BranchRow,
  type DrawerRow,
  type DrawerSummary,
  type Overview,
} from "@/lib/reports";
import { CloseTill } from "./CloseTill";
import styles from "./reports.module.css";

/**
 * What the business took.
 *
 * ── THE WINDOW IS WHOLE LOCAL DAYS, AND THE SERVER DECIDES IT ──────────────
 * `from` and `to` are passed through untouched; sales/reports.py reads them
 * as whole days in the shop's timezone, so `to=the 30th` includes the 30th.
 * Re-deriving that here would put a second, disagreeing definition of "this
 * month" in the product.
 *
 * ── A CALLER WHO MAY SEE LESS SEES LESS, NOT AN ERROR ──────────────────────
 * reports.branch and reports.organisation are different permissions, and a
 * branch manager is refused the consolidated ones. Each section is omitted
 * when its own call came back empty rather than the page failing whole.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Reports" };

/*
 * Named, and resolved by the server. See the banner on lib/reports.ts — the
 * dates used to be worked out here, in UTC, for a shop three hours east.
 */
const RANGES = [
  { key: "today", label: "Today" },
  { key: "week", label: "Last 7 days" },
  { key: "month", label: "This month" },
  { key: "year", label: "This year" },
] as const;

/**
 * A link to this page with one dimension changed and the other kept.
 *
 * ── THE PERIOD NAV USED TO DROP THE BRANCH ─────────────────────────────────
 *
 * Every report on this page takes `?branch=`, the permission model is built
 * around it, and nothing in the product ever set it: there was no picker, and
 * the period links were written as `/reports?range=…`, so a hand-typed branch
 * was discarded by the first click. Two dimensions in the URL means every
 * link on the screen has to carry both or one of them cannot be used.
 */
function href(range: string, branch?: number): string {
  const params = new URLSearchParams({ range });
  if (branch) params.set("branch", String(branch));
  return `/reports?${params}`;
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; branch?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { range = "today", branch } = await searchParams;
  /*
   * ⚠ NaN AND 0 BOTH HAVE TO BECOME undefined.
   *   `Number("")` is 0 and `Number("west")` is NaN, and either sent on as a
   *   branch id produces a window that matches nothing — a page of zeroes
   *   that reads as a shop with no trade rather than as a bad URL.
   */
  const asked = Number(branch);
  const selected = Number.isInteger(asked) && asked > 0 ? asked : undefined;

  const data = await dashboard({ range, branch: selected });

  /*
   * Named from the branch LIST, not from the comparison: a branch that has
   * not sold anything in the window is absent from the comparison, and that
   * is exactly when somebody is looking at it. Undefined when the id is not
   * one of theirs — the server already answered with nothing, and inventing
   * a name for it would dress a refusal up as an empty day.
   */
  const selectedName = data.branchOptions.find(
    (option) => option.id === selected,
  )?.branch_name;

  const canSeeOrganisation = may(me, PERM.reportsOrganisation);
  /*
   * Not security — the server refuses either way. This only decides whether
   * to offer a control that a cashier would be refused, which §6 asks for:
   * they learn who to ask instead of learning the feature does not exist.
   */
  const canClose = may(me, PERM.shiftClose);

  /*
   * ── THE NONCE, FOR THE ONE THING ON THIS PAGE THAT CANNOT USE A CLASS ────
   *
   * src/middleware.ts puts it on the request so Next can stamp its own
   * scripts; this reads it back for the bar widths below, which are per-row
   * data and so cannot be a static rule. A style="" attribute cannot carry a
   * nonce at all, which is why it is a <style> block instead.
   */
  const nonce = (await headers()).get("x-nonce") ?? undefined;

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Reports</h1>
          {/*
            ── THIS SENTENCE BECOMES UNTRUE THE MOMENT A BRANCH IS PICKED ───
            It said "Every branch, consolidated" unconditionally. With
            `?branch=` honoured by every figure on the page, that is a claim
            about the numbers below it that is simply wrong — and the reader
            has no other way to tell which branch they are looking at.
          */}
          <p className={styles.sub}>
            {selectedName
              ? `${selectedName} only. Every figure below is this branch's, except the branch comparison, which always covers the branches you can see.`
              : canSeeOrganisation
                ? "Every branch, consolidated. Figures are the server's own — nothing on this page is added up in a browser."
                : "The branches you are assigned to. Consolidated figures are an owner's to see."}
          </p>
          {selected !== undefined && selectedName === undefined ? (
            <p className={styles.sub}>
              That branch is not one of yours, so there is nothing to show for
              it. <Link href={href(range)}>Start again</Link>.
            </p>
          ) : null}
        </header>

        <nav className={styles.ranges} aria-label="Period">
          {RANGES.map((option) => (
            <Link
              key={option.key}
              href={href(option.key, selected)}
              className={`${styles.range} ${
                range === option.key ? styles.rangeOn : ""
              }`}
              aria-current={range === option.key ? "page" : undefined}
            >
              {option.label}
            </Link>
          ))}
        </nav>

        {/*
          Offered only where there is a choice to make. One branch is the
          common case for a shop starting out, and a picker with a single
          option in it is a control that does nothing.
        */}
        {data.branchOptions.length > 1 ? (
          <nav className={styles.branches} aria-label="Branch">
            <Link
              href={href(range)}
              className={`${styles.range} ${
                selected === undefined ? styles.rangeOn : ""
              }`}
              aria-current={selected === undefined ? "page" : undefined}
            >
              All branches
            </Link>
            {data.branchOptions.map((option) => (
              <Link
                key={option.id}
                href={href(range, option.id)}
                className={`${styles.range} ${
                  selected === option.id ? styles.rangeOn : ""
                }`}
                aria-current={selected === option.id ? "page" : undefined}
              >
                {option.branch_name}
              </Link>
            ))}
          </nav>
        ) : null}

        {!data.overview ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              No figures are available to you for this period.
            </p>
          </section>
        ) : (
          <Kpis overview={data.overview} />
        )}

        {data.registers.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Tills open now</h2>
            {/*
              ── THIS LEDE STATED A FORMULA THE COLUMNS DID NOT FOLLOW ──────
              It said "the cash it opened with, plus what it has taken, less
              change given", which stopped being the whole of it when cash
              movements were added and again when returns were. A reader
              adding three columns to reach a fourth that did not match had
              no way to find out why. Every term is a column now, and the
              sentence names all of them.
            */}
            <p className={styles.panelLede}>
              What should be in each drawer: what it opened with, plus what was
              handed over, less change handed back, plus or minus cash moved
              in or out, less any returns paid out of it. Counting against it
              is how a shift ends — a till cannot be closed without a count,
              and the difference is recorded against the shift.
            </p>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Till</th>
                    <th>Who</th>
                    <th className={styles.num}>Opened with</th>
                    <th className={styles.num}>Taken</th>
                    <th className={styles.num}>Change out</th>
                    <th className={styles.num}>Cash in</th>
                    <th className={styles.num}>Cash out</th>
                    <th className={styles.num}>Returned</th>
                    <th className={styles.num}>Should hold</th>
                    {canClose ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {data.registers.map((row) => (
                    <tr key={row.shift}>
                      <td>
                        <div className={styles.name}>{row.register_name}</div>
                        <div className={styles.meta}>{row.branch_name}</div>
                      </td>
                      <td>
                        <div>{row.operator_name}</div>
                        <div className={styles.meta}>
                          {row.transactions} sale
                          {row.transactions === 1 ? "" : "s"}
                        </div>
                      </td>
                      <td className={styles.num}>{ksh(row.opening_cash)}</td>
                      <td className={styles.num}>{ksh(row.cash_taken)}</td>
                      <td className={styles.num}>{ksh(row.change_given)}</td>
                      <td className={styles.num}>{ksh(row.paid_in)}</td>
                      <td className={styles.num}>{ksh(row.paid_out)}</td>
                      <td className={styles.num}>{ksh(row.refunded_cash)}</td>
                      <td className={`${styles.num} ${styles.strong}`}>
                        {ksh(row.expected_cash)}
                      </td>
                      {canClose ? (
                        <td className={styles.num}>
                          <CloseTill
                            shiftId={row.shift}
                            registerName={row.register_name}
                            expected={row.expected_cash}
                          />
                        </td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {data.drawers !== null ? (
          <Drawers
            rows={data.drawers.shifts}
            summary={data.drawers.summary}
            range={range}
          />
        ) : null}

        {data.alerts.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Running low</h2>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Branch</th>
                    <th className={styles.num}>On hand</th>
                    <th className={styles.num}>Reorder at</th>
                  </tr>
                </thead>
                <tbody>
                  {data.alerts.map((alert, index) => (
                    <tr key={`${alert.sku}-${index}`}>
                      <td>
                        <div className={styles.name}>{alert.product_name}</div>
                        <div className={styles.meta}>{alert.sku}</div>
                      </td>
                      <td>{alert.branch_name}</td>
                      <td className={`${styles.num} ${styles.low}`}>
                        {amount(alert.quantity)}
                      </td>
                      <td className={styles.num}>
                        {amount(alert.reorder_level)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {/*
          ── A TABLE, NOT A RANKED BAR, BECAUSE IT IS NO LONGER ONE FIGURE ──
          The bar compared turnover and nothing else. Profit is the figure
          the comparison exists to produce, and a bar can carry exactly one
          number — so the panel that most needed a second column was the one
          component that could not grow one.
        */}
        {data.branches.length > 1 ? (
          <BranchComparison
            rows={data.branches}
            range={range}
            selected={selected}
          />
        ) : null}

        <div className={styles.pair}>
          {data.methods.length > 0 ? (
            <Ranked
              title="How they paid"
              nonce={nonce}
              rows={data.methods.map((m) => ({
                key: m.method,
                name: m.method_label,
                note: `${m.count} payment${m.count === 1 ? "" : "s"}`,
                value: m.amount,
              }))}
            />
          ) : null}

          {data.cashiers.length > 0 ? (
            <Ranked
              title="By cashier"
              nonce={nonce}
              rows={data.cashiers.map((c) => ({
                key: c.cashier,
                name: c.cashier_name,
                note: `${c.transactions} sale${c.transactions === 1 ? "" : "s"}`,
                value: c.revenue,
              }))}
            />
          ) : null}
        </div>

        {data.products.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>What sold</h2>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th className={styles.num}>Units</th>
                    <th className={styles.num}>Revenue</th>
                    <th className={styles.num}>Gross profit</th>
                  </tr>
                </thead>
                <tbody>
                  {data.products.map((row) => (
                    <tr key={row.product}>
                      <td>
                        <div className={styles.name}>{row.product_name}</div>
                        <div className={styles.meta}>{row.sku}</div>
                      </td>
                      <td className={styles.num}>{amount(row.quantity)}</td>
                      <td className={styles.num}>{ksh(row.revenue)}</td>
                      <td
                        className={`${styles.num} ${
                          row.gross_profit.startsWith("-") ? styles.low : ""
                        }`}
                      >
                        {ksh(row.gross_profit)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={styles.footnote}>
              Revenue here is net of tax, so it will be lower than the till
              total for the same sales — that difference is the tax you
              collected on the shop&rsquo;s behalf and do not keep.
            </p>
          </section>
        ) : null}
      </div>
    </Shell>
  );
}

/**
 * Where the money is actually made — §4 "Branch comparison".
 *
 * ══════════════════════════════════════════════════════════════════════════
 * TURNOVER RANKS THE BRANCHES IN THE WRONG ORDER.
 *
 * This was a ranked bar of revenue. A branch shifting volume at a thin
 * margin sat above a quieter one earning more per shilling taken, and the
 * decision somebody opens a branch comparison to make — where stock, staff
 * and attention go — is answered by the second branch, not the first.
 *
 * Profit was two annotations away in sales/reports.py the whole time.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Rows are links, so this doubles as the way into a branch: the chips above
 * the page are for a branch that has not traded, and this is for the one
 * somebody just spotted a problem in.
 */
function BranchComparison({
  rows,
  range,
  selected,
}: {
  rows: BranchRow[];
  range: string;
  selected?: number;
}) {
  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>By branch</h2>
      <p className={styles.panelLede}>
        Ranked on what was taken, and read on what was earned — the two orders
        are not the same. This panel always covers every branch you can see,
        including when one is selected above.
      </p>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Branch</th>
              <th className={styles.num}>Sales</th>
              <th className={styles.num}>Taken</th>
              <th className={styles.num}>Net of tax</th>
              <th className={styles.num}>Gross profit</th>
              <th className={styles.num}>Margin</th>
              <th className={styles.num}>Refunded</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.branch}>
                <td>
                  <Link
                    href={href(range, row.branch)}
                    className={
                      selected === row.branch ? styles.pickedName : styles.name
                    }
                  >
                    {row.branch_name}
                  </Link>
                  {row.transactions === 0 ? (
                    <div className={styles.meta}>
                      Nothing sold in this period
                    </div>
                  ) : null}
                </td>
                <td className={styles.num}>{row.transactions}</td>
                <td className={styles.num}>{ksh(row.revenue)}</td>
                <td className={styles.num}>{ksh(row.net_revenue)}</td>
                <td
                  className={`${styles.num} ${styles.strong} ${
                    row.gross_profit.startsWith("-") ? styles.low : ""
                  }`}
                >
                  {ksh(row.gross_profit)}
                </td>
                <td className={styles.num}>{margin(row)}</td>
                <td
                  className={`${styles.num} ${
                    Number(row.refunded) > 0 ? styles.low : ""
                  }`}
                >
                  {ksh(row.refunded)}
                  {row.refunds > 0 ? (
                    <div className={styles.meta}>
                      {row.refunds} return{row.refunds === 1 ? "" : "s"}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.footnote}>
        Margin is gross profit over revenue net of tax, which is the base it
        was earned on — figuring it against the till total would understate
        every branch by the VAT it collected and does not keep.
      </p>
    </section>
  );
}

/**
 * Gross profit as a share of what the branch actually earned.
 *
 * ⚠ THE ONE FIGURE ON THIS PAGE A BROWSER WORKS OUT, AND IT IS A RATIO.
 *   Every money figure arrives as a string and stays one, because a Decimal
 *   through a float stops adding up. A percentage is not money and is never
 *   summed with anything, so parsing here cannot corrupt a total — and the
 *   server has no business computing a display rounding.
 *
 *   Net revenue, not revenue: the profit was earned on the money the branch
 *   keeps, and dividing by the till total would understate every branch by
 *   the tax it collected on somebody else's behalf.
 */
function margin(row: BranchRow): string {
  const base = Number(row.net_revenue);
  // A branch that only refunded has no base to divide by, and 0/0 renders as
  // "NaN%" — which looks like a bug in the shop's figures rather than an
  // absent one.
  if (!Number.isFinite(base) || base <= 0) return "—";
  return `${((Number(row.gross_profit) / base) * 100).toFixed(1)}%`;
}

/**
 * The drawers that have already been counted, and by how much each was out.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE VARIANCE WAS PRODUCED AND THEN UNREADABLE, WHICH IS MOST OF THE WAY TO
 * NOT PRODUCING IT.
 *
 * "Tills open now" above lists OPEN shifts, so a drawer's figures were
 * visible for exactly as long as the shift had no variance, and disappeared
 * at the moment it acquired one. The number a till exists to produce was
 * computed, put in a notification, and then had nowhere to be looked at.
 *
 * So "is it always the same till" had no answer in the product. This is that
 * answer, and it is the reason a branch auditor — who holds reports.branch
 * and cannot close a till at all — has a screen worth opening.
 * ══════════════════════════════════════════════════════════════════════════
 */
function Drawers({
  rows,
  summary,
  range,
}: {
  rows: DrawerRow[];
  summary: DrawerSummary;
  range: string;
}) {
  /*
   * Written out per period rather than lower-casing the nav label, which
   * produced "No till was closed in last 7 days".
   */
  const WHEN: Record<string, string> = {
    today: "today",
    week: "in the last 7 days",
    month: "this month",
    year: "this year",
  };
  const when = WHEN[range] ?? "in this period";

  if (rows.length === 0) {
    return (
      <section className={styles.panel}>
        <h2 className={styles.panelTitle}>Drawers counted</h2>
        <p className={styles.empty}>
          No till was closed {when}. A drawer appears here once somebody
          counts it.
        </p>
      </section>
    );
  }

  /*
   * ── THE NET IS SHOWN LAST AND NAMED AS A NET ─────────────────────────────
   *
   * One till 500 short and another 500 over nets to zero. A panel that led
   * with that number would report a day as reconciled while one drawer is
   * missing money and another holds money nothing accounts for — two
   * problems rather than none. So the counts come first and the net carries
   * the word.
   */
  const net = Number(summary.net_variance);
  const unbalanced = summary.short + summary.over;

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>Drawers counted</h2>
      <p className={styles.panelLede}>
        Every till closed in this period, and what each came out at against
        what it should have held. A count cannot be redone — a second one only
        agrees with itself — so what is here is what was found.
      </p>

      <dl className={styles.tally}>
        <div>
          <dt>Counted</dt>
          <dd>{summary.counted}</dd>
        </div>
        <div>
          <dt>Balanced</dt>
          <dd className={summary.balanced > 0 ? styles.exact : undefined}>
            {summary.balanced}
          </dd>
        </div>
        <div>
          <dt>Short</dt>
          <dd className={summary.short > 0 ? styles.short : undefined}>
            {summary.short}
          </dd>
        </div>
        <div>
          <dt>Over</dt>
          <dd className={summary.over > 0 ? styles.over : undefined}>
            {summary.over}
          </dd>
        </div>
        <div>
          <dt>Worst shortfall</dt>
          <dd className={Number(summary.worst_short) < 0 ? styles.short : undefined}>
            {/*
              The magnitude, not the signed figure: the label already says
              "shortfall", and "-500.00" under it reads as a double negative.
            */}
            {Number(summary.worst_short) < 0
              ? ksh(summary.worst_short.slice(1))
              : "—"}
          </dd>
        </div>
        <div>
          <dt>Net, over all tills</dt>
          <dd className={net === 0 ? undefined : net < 0 ? styles.short : styles.over}>
            {net === 0 ? "Nil" : ksh(summary.net_variance)}
          </dd>
        </div>
      </dl>

      {unbalanced > 0 && net === 0 ? (
        <p className={styles.footnote}>
          The net is nil and {unbalanced} drawer
          {unbalanced === 1 ? "" : "s"} did not balance. Tills that cancel each
          other out are two problems, not none — the usual cause is a sale rung
          up on the wrong till.
        </p>
      ) : null}

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Till</th>
              <th>On the till</th>
              <th className={styles.num}>Opened with</th>
              <th className={styles.num}>Taken</th>
              <th className={styles.num}>Change out</th>
              <th className={styles.num}>Cash in</th>
              <th className={styles.num}>Cash out</th>
              <th className={styles.num}>Returned</th>
              <th className={styles.num}>Should have held</th>
              <th className={styles.num}>Counted</th>
              <th className={styles.num}>Out by</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const variance = row.variance === null ? null : Number(row.variance);
              return (
                <tr key={row.shift}>
                  <td>
                    <div className={styles.name}>{row.register_name}</div>
                    <div className={styles.meta}>
                      {row.branch_name} · {closedAt(row.closed_at)}
                    </div>
                  </td>
                  <td>
                    <div>{row.operator_name}</div>
                    {row.note ? (
                      <div className={styles.shiftNote} title={row.note}>
                        {row.note}
                      </div>
                    ) : null}
                  </td>
                  <td className={styles.num}>{ksh(row.opening_cash)}</td>
                  <td className={styles.num}>{ksh(row.cash_taken)}</td>
                  <td className={styles.num}>{ksh(row.change_given)}</td>
                  <td className={styles.num}>{ksh(row.paid_in)}</td>
                  <td className={styles.num}>{ksh(row.paid_out)}</td>
                  <td className={styles.num}>{ksh(row.refunded_cash)}</td>
                  <td className={`${styles.num} ${styles.strong}`}>
                    {ksh(row.expected_cash)}
                  </td>
                  <td className={styles.num}>
                    {/*
                      An em dash, never a zero. A drawer closed before the
                      count existed was never counted, and printing 0.00 here
                      would read as "checked, and correct" — the most
                      flattering possible lie about it.
                    */}
                    {row.counted_cash === null ? "—" : ksh(row.counted_cash)}
                  </td>
                  <td
                    className={`${styles.num} ${
                      variance === null
                        ? ""
                        : variance === 0
                          ? styles.exact
                          : variance < 0
                            ? styles.short
                            : styles.over
                    }`}
                  >
                    {variance === null
                      ? "Not counted"
                      : variance === 0
                        ? "Balanced"
                        : `${variance < 0 ? "Short" : "Over"} ${ksh(
                            String(Math.abs(variance)),
                          )}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {summary.truncated ? (
        <p className={styles.footnote}>
          Showing the {rows.length} most recently counted of{" "}
          {summary.closed_in_window}. The tally above describes these
          {" "}{rows.length} and not the whole period — narrow the window to
          account for all of them.
        </p>
      ) : null}

      {rows.some((row) => row.uncounted) ? (
        <p className={styles.footnote}>
          A drawer marked <strong>not counted</strong> was closed before this
          product required a count. There is no variance for it and never will
          be; it is listed so the gap is visible rather than absent.
        </p>
      ) : null}
    </section>
  );
}

/**
 * The close time, in the shop's own words.
 *
 * ⚠ FIXED TIMEZONE AND LOCALE, NOT THE READER'S.
 *   `toLocaleString` with neither reads the environment — which is UTC in the
 *   container this page renders in, so a till counted at 00:30 in Nairobi
 *   would be printed as the previous day. A reconciliation screen whose dates
 *   disagree with the shop's own day is worse than one with no dates.
 */
function closedAt(when: string): string {
  return new Date(when).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * The figures somebody opens this page for.
 *
 * Six, not sixteen. A strip of big numbers only works if a reader can hold
 * all of them at once, and the rest of the page is where the detail lives.
 */
function Kpis({ overview }: { overview: Overview }) {
  const tiles = [
    { label: "Taken", value: ksh(overview.revenue), lead: true },
    { label: "Gross profit", value: ksh(overview.gross_profit), lead: true },
    { label: "Sales", value: String(overview.transactions) },
    { label: "Average basket", value: ksh(overview.average_basket) },
    { label: "Tax collected", value: ksh(overview.tax_collected) },
    { label: "Refunded", value: ksh(overview.refunded) },
  ];

  return (
    <section className={styles.kpis}>
      {tiles.map((tile) => (
        <div
          key={tile.label}
          className={`${styles.kpi} ${tile.lead ? styles.kpiLead : ""}`}
        >
          <div className={styles.kpiLabel}>{tile.label}</div>
          <div className={styles.kpiValue}>{tile.value}</div>
        </div>
      ))}
    </section>
  );
}

/**
 * A ranked list with a bar.
 *
 * The bar is scaled to the largest row, and the largest row's own label names
 * its value — so the bar adds a comparison and never becomes the only place a
 * figure appears.
 */
function Ranked({
  title,
  rows,
  nonce,
}: {
  title: string;
  rows: { key: string | number; name: string; note: string; value: string }[];
  nonce?: string;
}) {
  const widths = rows.map((row) => Number(row.value) || 0);
  const top = Math.max(...widths, 1);

  /*
   * ── A NONCE'D <style> BLOCK, NOT A style="" ATTRIBUTE ────────────────────
   *
   * The width is per-row data, so it cannot be a class written in advance.
   * The obvious `style={{ width }}` is a style ATTRIBUTE, and an attribute
   * cannot carry a nonce — under `style-src 'self' 'nonce-…'` every bar would
   * silently collapse to nothing while the figures beside them stayed right,
   * which is the worst kind of broken chart: still readable, quietly wrong.
   *
   * So the widths are emitted as real rules, nonced like everything else.
   * `id` scopes them to this list, because three of these render on one page.
   */
  const id = `rank-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  const rules = rows
    .map((row, index) => {
      const percent = Math.max((widths[index]! / top) * 100, 2);
      return `#${id} li:nth-child(${index + 1}) .${styles.bar}{width:${percent.toFixed(2)}%}`;
    })
    .join("");

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{title}</h2>
      <style nonce={nonce}>{rules}</style>
      <ul className={styles.ranked} id={id}>
        {rows.map((row) => (
          <li key={row.key} className={styles.rank}>
            <div className={styles.rankTop}>
              <span className={styles.name}>{row.name}</span>
              <span className={styles.rankValue}>{ksh(row.value)}</span>
            </div>
            <div className={styles.bar} />
            <div className={styles.meta}>{row.note}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}
