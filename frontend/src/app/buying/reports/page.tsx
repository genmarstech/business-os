import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { amount, ksh } from "@/lib/money";
import {
  buying,
  type BuyingOverview,
  type Outstanding,
  type ReliabilityRow,
} from "@/lib/reports";
import { me as whoAmI } from "@/lib/session";
import styles from "./buyingReports.module.css";

/**
 * What the shop is spending, and on whom.
 *
 * ── THE POSITION COMES FIRST, AND IT HAS NO PERIOD ────────────────────────
 * "What have we committed to and not received" is the question somebody
 * actually opens this page with, and it is true as of now rather than over a
 * window. It sits above the period controls so it cannot be read as a figure
 * for the selected month — which is exactly how it would be misread if it
 * sat in the strip with the others.
 *
 * ── ORDERED IS NOT SPENT ──────────────────────────────────────────────────
 * Both figures are shown and both are labelled. Presenting ordered value as
 * spend overstates every quarter with stock in transit, and overstates it
 * permanently wherever an order was cancelled — procurement/reports.py keeps
 * them apart and so does this.
 *
 * ── A CALLER WHO MAY SEE LESS SEES LESS, NOT AN ERROR ─────────────────────
 * Every figure here is confined to the branches the caller is assigned to,
 * by the server. A branch manager gets their own branch's numbers rather
 * than a refusal, and each section is omitted when its own call came back
 * empty instead of the page failing whole.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Buying reports" };

const RANGES = [
  { key: "week", label: "Last 7 days" },
  { key: "month", label: "This month" },
  { key: "year", label: "This year" },
] as const;

function when(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default async function BuyingReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ range?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { range = "month" } = await searchParams;
  const data = await buying({ range });

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Buying</p>
          <h1 className={styles.title}>What we are spending</h1>
          <p className={styles.sub}>
            Figures are the server&rsquo;s own and cover the branches you work
            at. Nothing on this page is added up in a browser.
          </p>
        </header>

        {data.outstanding ? <Position position={data.outstanding} /> : null}

        <nav className={styles.ranges} aria-label="Period">
          {RANGES.map((option) => (
            <Link
              key={option.key}
              href={`/buying/reports?range=${option.key}`}
              className={`${styles.range} ${
                range === option.key ? (styles.rangeOn ?? "") : ""
              }`}
              aria-current={range === option.key ? "page" : undefined}
            >
              {option.label}
            </Link>
          ))}
        </nav>

        {data.overview ? (
          <Kpis overview={data.overview} />
        ) : (
          <section className={styles.panel}>
            <p className={styles.empty}>
              No buying figures are available to you for this period.
            </p>
          </section>
        )}

        {data.outstanding && data.outstanding.orders.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Waiting on a delivery</h2>
            <p className={styles.panelLede}>
              Ordered and not yet received, valued at the price on the order.
              Drafts are not here: nobody outside the shop has been asked for
              them yet.
            </p>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>Supplier</th>
                    <th>Expected</th>
                    <th className={styles.num}>Still owed</th>
                  </tr>
                </thead>
                <tbody>
                  {data.outstanding.orders.map((order) => (
                    <tr
                      key={order.order}
                      className={order.overdue ? (styles.overdueRow ?? "") : ""}
                    >
                      <td>
                        <div className={styles.name}>#{order.number}</div>
                        <div className={styles.meta}>
                          {order.branch_name} · {order.status_label}
                        </div>
                      </td>
                      <td>{order.supplier_name}</td>
                      <td>
                        {when(order.expected_at)}
                        {order.overdue ? (
                          <div className={styles.meta}>
                            {order.days_late} day
                            {order.days_late === 1 ? "" : "s"} late
                          </div>
                        ) : order.expected_at === null ? (
                          /* Unscheduled is not overdue. Saying so stops
                             somebody chasing a supplier who was never given
                             a date. */
                          <div className={styles.meta}>No date given</div>
                        ) : null}
                      </td>
                      <td className={`${styles.num} ${styles.strong}`}>
                        {ksh(order.owed)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {data.suppliers.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Where the money went</h2>
            <p className={styles.panelLede}>
              What each supplier was paid for goods that actually arrived,
              beside what was ordered from them. The two differ whenever
              something is still in transit or an order was cancelled.
            </p>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Supplier</th>
                    <th className={styles.num}>Received</th>
                    <th className={styles.num}>Orders</th>
                    <th className={styles.num}>Ordered</th>
                  </tr>
                </thead>
                <tbody>
                  {data.suppliers.map((row) => (
                    <tr key={row.supplier}>
                      <td className={styles.name}>{row.supplier_name}</td>
                      <td className={`${styles.num} ${styles.strong}`}>
                        {ksh(row.received_value)}
                      </td>
                      <td className={styles.num}>{row.orders}</td>
                      <td className={`${styles.num} ${styles.quiet}`}>
                        {ksh(row.ordered_value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        {data.reliability.length > 0 ? (
          <Reliability rows={data.reliability} />
        ) : null}

        {data.products.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>What we buy most</h2>
            <p className={styles.panelLede}>
              By what it costs, not by how much of it arrives — a pallet of
              the cheapest thing in the shop is not the line worth
              renegotiating.
            </p>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Product</th>
                    <th className={styles.num}>Received</th>
                    <th className={styles.num}>Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.products.map((row, index) => (
                    <tr key={`${row.sku}-${index}`}>
                      <td>
                        <div className={styles.name}>{row.product_name}</div>
                        <div className={styles.meta}>{row.sku}</div>
                      </td>
                      <td className={styles.num}>{amount(row.quantity)}</td>
                      <td className={`${styles.num} ${styles.strong}`}>
                        {ksh(row.value)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ) : null}

        <p className={styles.footnote}>
          Purchase tax is not modelled. What a business reclaims of input VAT
          is an accounting question with a filing attached, and a figure here
          would be read as an answer to it.
        </p>
      </div>
    </Shell>
  );
}

/**
 * The obligations, as of now.
 *
 * Deliberately two tiles and not six: a position is one sentence — this much
 * is owed, this much of it is late — and surrounding it with period figures
 * is what makes somebody read it as one.
 */
function Position({ position }: { position: Outstanding }) {
  const nothingLate = position.overdue === "0.00";

  return (
    <section className={styles.position} aria-label="Outstanding orders">
      <div className={styles.owed}>
        <p className={styles.positionLabel}>Committed, not yet delivered</p>
        <div className={styles.positionValue}>{ksh(position.committed)}</div>
        <p className={styles.positionNote}>
          As of today, across {position.orders.length} order
          {position.orders.length === 1 ? "" : "s"}.
        </p>
      </div>

      <div className={nothingLate ? styles.owed : styles.late}>
        <p className={styles.positionLabel}>Past its expected date</p>
        <div
          className={`${styles.positionValue} ${
            nothingLate ? "" : (styles.lateFigure ?? "")
          }`}
        >
          {ksh(position.overdue)}
        </div>
        <p className={styles.positionNote}>
          {nothingLate
            ? "Nothing is late."
            : "Somebody is waiting on this. An order with no expected date is not counted here."}
        </p>
      </div>
    </section>
  );
}

function Kpis({ overview }: { overview: BuyingOverview }) {
  const tiles = [
    {
      label: "Spent",
      value: ksh(overview.received_value),
      note: `${overview.deliveries} deliver${overview.deliveries === 1 ? "y" : "ies"}`,
      lead: true,
    },
    {
      label: "Ordered",
      value: ksh(overview.ordered_value),
      note: `${overview.orders_raised} order${overview.orders_raised === 1 ? "" : "s"} raised`,
      lead: true,
    },
    { label: "Suppliers used", value: String(overview.suppliers_used) },
    { label: "Lines received", value: String(overview.lines_received) },
    {
      label: "Cancelled",
      value: ksh(overview.cancelled_value),
      note: `${overview.orders_cancelled} order${overview.orders_cancelled === 1 ? "" : "s"}`,
    },
  ];

  return (
    <section className={styles.kpis}>
      {tiles.map((tile) => (
        <div
          key={tile.label}
          className={`${styles.kpi} ${tile.lead ? (styles.kpiLead ?? "") : ""}`}
        >
          <div className={styles.kpiLabel}>{tile.label}</div>
          <div className={styles.kpiValue}>{tile.value}</div>
          {tile.note ? <div className={styles.kpiNote}>{tile.note}</div> : null}
        </div>
      ))}
    </section>
  );
}

/**
 * Who delivers when they said they would.
 *
 * The rate is a chip rather than a bare percentage, because 67% and 100% read
 * the same at a glance in a column of digits and the whole point of this
 * table is to be glanceable. A supplier with nothing judgeable gets a chip
 * saying so — not a zero, which would read as "never on time".
 */
function Reliability({ rows }: { rows: ReliabilityRow[] }) {
  function chip(row: ReliabilityRow): { text: string; className: string } {
    if (row.on_time_rate === null) {
      return { text: "No dates set", className: styles.chipNone ?? "" };
    }
    if (row.on_time_rate >= 90) {
      return { text: `${row.on_time_rate}% on time`, className: styles.chipGood ?? "" };
    }
    if (row.on_time_rate >= 60) {
      return { text: `${row.on_time_rate}% on time`, className: styles.chipWarn ?? "" };
    }
    return { text: `${row.on_time_rate}% on time`, className: styles.chipBad ?? "" };
  }

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>Who turns up when they say</h2>
      <p className={styles.panelLede}>
        Over orders fully received in this period, against the date the shop
        expected them. An order still in transit has no verdict yet, and one
        raised without an expected date cannot be judged at all — both are
        counted separately rather than quietly improving the figure.
      </p>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Supplier</th>
              <th>Record</th>
              <th className={styles.num}>On time</th>
              <th className={styles.num}>Late</th>
              <th className={styles.num}>Avg. days late</th>
              <th className={styles.num}>No date</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const verdict = chip(row);
              return (
                <tr key={row.supplier}>
                  <td className={styles.name}>{row.supplier_name}</td>
                  <td>
                    <span className={`${styles.chip} ${verdict.className}`}>
                      {verdict.text}
                    </span>
                  </td>
                  <td className={styles.num}>{row.on_time}</td>
                  <td className={styles.num}>{row.late}</td>
                  <td className={styles.num}>
                    {row.average_days_late === null ? (
                      <span className={styles.quiet}>—</span>
                    ) : (
                      row.average_days_late.toFixed(1)
                    )}
                  </td>
                  <td className={`${styles.num} ${styles.quiet}`}>
                    {row.unscheduled}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
