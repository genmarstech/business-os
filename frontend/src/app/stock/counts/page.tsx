import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { amount } from "@/lib/money";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { CountSheet, OpenCountForm } from "./forms";
import type { Branch, Count, Stock } from "./types";
import styles from "./counts.module.css";

/**
 * Stock takes.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THIS SCREEN SHOWS THE EXPECTED FIGURE WHILE COUNTING. THE TILL DOES NOT.
 *
 * That is not an inconsistency, and it is not a leak. The till's count is
 * blind because somebody shown the number the system believes will count to
 * it — a shelf of identical boxes is hard to count and "it says 50" is a
 * powerful suggestion — and the whole value of a stock take is that it can
 * disagree.
 *
 * Blinding it here would be theatre. The only principals who reach this
 * screen are subscribers, and the two who hold `inventory.count` — owner and
 * admin — both hold `inventory.count.close` as well. There is nothing to
 * hide from somebody who can reveal it by clicking one tab. A screen that
 * pretended otherwise would teach a false confidence in its own numbers,
 * which is the one thing Charter 04 §IV is about.
 *
 * So it says what it is instead: a count you take and sign yourself checks
 * the books, and a count somebody else takes blind checks the shop. The
 * second is a till away, and the page points at it.
 * ══════════════════════════════════════════════════════════════════════════
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Stock takes" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

function when(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString("en-KE", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default async function StockCountsPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [countPage, branchPage, stockPage] = await Promise.all([
    getOrNull<Page<Count>>("/invt/stock-counts/"),
    getOrNull<Page<Branch>>("/brn/branch/"),
    getOrNull<Page<Stock>>("/invt/inventory/"),
  ]);

  const counts = rows(countPage);
  const stock = rows(stockPage);

  // Only branches this caller may actually start a count at. Offering the
  // rest would be offering a refusal — and the server now checks the branch
  // as well as the tenant, so it would genuinely be one.
  const branches = rows(branchPage).filter(
    (branch) => branch.is_active && may(me, PERM.inventoryCount, branch.id),
  );

  const open = counts.filter((count) => count.status === "open");
  const done = counts.filter((count) => count.status !== "open");

  // Branches already being counted. One open count per branch is enforced in
  // the database, so offering a second here is offering an error message.
  const busy = new Set(open.map((count) => count.branch));
  const free = branches.filter((branch) => !busy.has(branch.id));

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Inventory</p>
          <h1 className={styles.title}>Stock takes</h1>
          <p className={styles.sub}>
            Counting a shelf and writing down what is on it. Nothing moves
            while a count is open — the lines record a disagreement, and the
            difference is booked as{" "}
            <Link className={styles.link} href="/stock/history">
              movements
            </Link>{" "}
            all at once when the count is closed, so a shortfall is reviewed
            as a sheet rather than arriving as a trickle of adjustments
            nobody saw whole.
          </p>
        </header>

        {open.length === 0 && done.length === 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Nothing counted yet</h2>
            <p className={styles.panelLede}>
              A stock take is the only thing that tells you whether the
              figures on{" "}
              <Link className={styles.link} href="/stock">
                Stock
              </Link>{" "}
              are true. Everything else in this system describes what{" "}
              <em>should</em> have happened to a quantity.
            </p>
          </section>
        ) : null}

        {open.map((count) => (
          <CountCard
            key={count.id}
            count={count}
            stock={stock.filter((row) => row.branch === count.branch)}
            canCount={may(me, PERM.inventoryCount, count.branch)}
            canClose={may(me, PERM.inventoryCountClose, count.branch)}
          />
        ))}

        {free.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Start a count</h2>
            <p className={styles.panelLede}>
              One branch at a time, and one count per branch: two people
              counting the same shelves produce two contradictory answers and
              no way to say which came first.
            </p>
            <OpenCountForm branches={free} />
            <p className={styles.aside}>
              <strong>The stronger version of this is at a till.</strong> An
              inventory clerk counting from the aisle never sees the expected
              figure, so their count cannot drift towards it, and you sign off
              what they found. A count you take and sign yourself checks the
              books. A blind count checks the shop.
            </p>
          </section>
        ) : branches.length > 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Every branch you can count is already being counted. Close or
              abandon the open count first.
            </p>
          </section>
        ) : null}

        {done.length > 0 ? (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Finished</h2>
              <p className={styles.empty}>
                Closed and abandoned counts, kept as they were taken. A
                miscount is corrected by counting again, never by editing one
                of these.
              </p>
            </section>
            {done.map((count) => (
              <FinishedCount key={count.id} count={count} />
            ))}
          </>
        ) : null}
      </div>
    </Shell>
  );
}

function Tally({ count }: { count: Count }) {
  const s = count.summary;
  return (
    <ul className={styles.tally}>
      <li>
        <span className={styles.tallyNumber}>{s.counted}</span>
        <span className={styles.tallyLabel}>counted</span>
      </li>
      <li>
        <span className={styles.tallyNumber}>{s.agreed}</span>
        <span className={styles.tallyLabel}>agreed</span>
      </li>
      <li className={s.short > 0 ? styles.bad : ""}>
        <span className={styles.tallyNumber}>{s.short}</span>
        <span className={styles.tallyLabel}>
          short{s.short > 0 ? ` · ${amount(s.units_short)}` : ""}
        </span>
      </li>
      <li className={s.over > 0 ? styles.odd : ""}>
        <span className={styles.tallyNumber}>{s.over}</span>
        <span className={styles.tallyLabel}>
          over{s.over > 0 ? ` · ${amount(s.units_over)}` : ""}
        </span>
      </li>
    </ul>
  );
}

function CountCard({
  count,
  stock,
  canCount,
  canClose,
}: {
  count: Count;
  stock: Stock[];
  canCount: boolean;
  canClose: boolean;
}) {
  return (
    <section
      className={styles.panel}
      aria-label={`Count ${count.number} at ${count.branch_name}`}
    >
      <div className={styles.countHead}>
        <div>
          <h2 className={styles.panelTitle}>{count.branch_name}</h2>
          <div className={styles.meta}>
            Count #{count.number} · opened {when(count.opened_at)} by{" "}
            {count.opened_by_name || "somebody since removed"}
          </div>
        </div>
        <span className={`${styles.badge} ${styles.openBadge}`}>
          {count.status_label}
        </span>
      </div>

      {count.note ? <p className={styles.meta}>{count.note}</p> : null}

      <Tally count={count} />

      <CountSheet
        count={count}
        stock={stock}
        canCount={canCount}
        canClose={canClose}
      />
    </section>
  );
}

function FinishedCount({ count }: { count: Count }) {
  const abandoned = count.status === "abandoned";

  return (
    <section
      className={`${styles.panel} ${styles.closed}`}
      aria-label={`Count ${count.number} at ${count.branch_name}`}
    >
      <div className={styles.countHead}>
        <div>
          <h2 className={styles.panelTitle}>{count.branch_name}</h2>
          <div className={styles.meta}>
            Count #{count.number} · {count.opened_by_name || "—"} counted,{" "}
            {count.closed_by_name || "—"}{" "}
            {abandoned ? "abandoned it" : "signed it off"} {when(count.closed_at)}
          </div>
        </div>
        <span
          className={`${styles.badge} ${
            abandoned ? styles.abandonedBadge : styles.doneBadge
          }`}
        >
          {count.status_label}
        </span>
      </div>

      {count.note ? <p className={styles.meta}>{count.note}</p> : null}

      {abandoned ? (
        <p className={styles.empty}>
          Nothing was booked. Whatever was counted is kept, because
          &ldquo;somebody began counting the back room on Tuesday and
          stopped&rdquo; is a thing worth being able to see.
        </p>
      ) : null}

      {count.lines.length > 0 ? (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Product</th>
                <th className={styles.num}>System</th>
                <th className={styles.num}>Counted</th>
                <th className={styles.num}>Difference</th>
                <th>Counted by</th>
              </tr>
            </thead>
            <tbody>
              {count.lines.map((line) => {
                const variance = Number(line.variance);
                return (
                  <tr key={line.id}>
                    <td>
                      <div className={styles.name}>{line.product_name}</div>
                      <div className={styles.meta}>{line.product_sku}</div>
                    </td>
                    <td className={styles.num}>
                      {amount(line.expected_quantity)}
                    </td>
                    <td className={styles.num}>
                      {amount(line.counted_quantity)}
                    </td>
                    <td
                      className={`${styles.num} ${
                        variance < 0
                          ? styles.bad
                          : variance > 0
                            ? styles.odd
                            : ""
                      }`}
                    >
                      {variance > 0 ? "+" : ""}
                      {amount(line.variance)}
                    </td>
                    <td>
                      <div className={styles.meta}>
                        {line.counted_by_name || "—"}
                        {line.note ? ` · ${line.note}` : ""}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
