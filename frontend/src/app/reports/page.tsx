import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import {
  amount,
  dashboard,
  ksh,
  type Overview,
} from "@/lib/reports";
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

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string; branch?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { range = "today", branch } = await searchParams;
  const data = await dashboard({
    range,
    branch: branch ? Number(branch) : undefined,
  });

  const canSeeOrganisation = may(me, PERM.reportsOrganisation);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Reports</h1>
          <p className={styles.sub}>
            {canSeeOrganisation
              ? "Every branch, consolidated. Figures are the server's own — nothing on this page is added up in a browser."
              : "The branches you are assigned to. Consolidated figures are an owner's to see."}
          </p>
        </header>

        <nav className={styles.ranges} aria-label="Period">
          {RANGES.map((option) => (
            <Link
              key={option.key}
              href={`/reports?range=${option.key}`}
              className={`${styles.range} ${
                range === option.key ? styles.rangeOn : ""
              }`}
              aria-current={range === option.key ? "page" : undefined}
            >
              {option.label}
            </Link>
          ))}
        </nav>

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
            <p className={styles.panelLede}>
              What should be in each drawer: the cash it opened with, plus what
              it has taken, less change given. Count against this at the close.
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
                    <th className={styles.num}>Should hold</th>
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
                      <td className={`${styles.num} ${styles.strong}`}>
                        {ksh(row.expected_cash)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
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

        <div className={styles.pair}>
          {data.branches.length > 0 ? (
            <Ranked
              title="By branch"
              rows={data.branches.map((b) => ({
                key: b.branch,
                name: b.branch_name,
                note: `${b.transactions} sale${b.transactions === 1 ? "" : "s"}`,
                value: b.revenue,
              }))}
            />
          ) : null}

          {data.methods.length > 0 ? (
            <Ranked
              title="How they paid"
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
}: {
  title: string;
  rows: { key: string | number; name: string; note: string; value: string }[];
}) {
  const widths = rows.map((row) => Number(row.value) || 0);
  const top = Math.max(...widths, 1);

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{title}</h2>
      <ul className={styles.ranked}>
        {rows.map((row, index) => (
          <li key={row.key} className={styles.rank}>
            <div className={styles.rankTop}>
              <span className={styles.name}>{row.name}</span>
              <span className={styles.rankValue}>{ksh(row.value)}</span>
            </div>
            <div
              className={styles.bar}
              style={{ width: `${Math.max((widths[index]! / top) * 100, 2)}%` }}
            />
            <div className={styles.meta}>{row.note}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}
