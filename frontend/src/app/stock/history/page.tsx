import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { amount } from "@/lib/money";
import { PERM, may, me as whoAmI } from "@/lib/session";
import styles from "../stock.module.css";

/**
 * Where the stock went.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE TRAIL WAS BEING WRITTEN AND NEVER SHOWN.
 *
 * Every quantity change in this system writes a StockMovement — the sale
 * path and the adjustment path both refuse to move a number without one, and
 * there are tests holding that. And no screen read any of it. A shopkeeper
 * could see "48 milk" and had no way to answer the question they actually
 * have, which is "it was 50 yesterday, where did two go".
 *
 * A count is a number. This is the account of it.
 * ══════════════════════════════════════════════════════════════════════════
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Stock history" };

type Movement = {
  id: number;
  branch_name: string;
  product_name: string;
  product_sku: string;
  movement_type: string;
  quantity_before: string;
  quantity_after: string;
  reference?: string | null;
  reason?: string | null;
  created_at: string;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

/**
 * What each movement kind means in a shop's own words.
 *
 * The stored values are the model's — PURCHASE, SALE, ADJUSTMENT and the
 * rest. Nobody running a counter thinks in those, and "PURCHASE" for a
 * delivery actively misleads.
 */
const KIND: Record<string, { label: string; tone: "in" | "out" | "flat" }> = {
  PURCHASE: { label: "Delivery", tone: "in" },
  SALE: { label: "Sold", tone: "out" },
  RETURN: { label: "Returned", tone: "in" },
  DAMAGE: { label: "Damaged", tone: "out" },
  THEFT: { label: "Missing", tone: "out" },
  TRANSFER_IN: { label: "Transferred in", tone: "in" },
  TRANSFER_OUT: { label: "Transferred out", tone: "out" },
  ADJUSTMENT: { label: "Count corrected", tone: "flat" },
  OTHER: { label: "Other", tone: "flat" },
};

export default async function StockHistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ product?: string; branch?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { product, branch } = await searchParams;

  const movements = rows(
    await getOrNull<Page<Movement>>("/invt/stock-movements/"),
  )
    // Filtered here rather than by query string: the endpoint is already
    // scoped to the caller's tenant, and these two narrowings are a
    // convenience on top of that rather than a security boundary.
    .filter((m) => !product || m.product_sku === product)
    .filter((m) => !branch || m.branch_name === branch)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  const canSee = may(me, PERM.inventoryView);

  const products = [...new Set(movements.map((m) => m.product_sku))];
  const branches = [...new Set(movements.map((m) => m.branch_name))];

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Inventory</p>
          <h1 className={styles.title}>Stock history</h1>
          <p className={styles.sub}>
            Every movement, newest first. A quantity in this system never
            changes without one of these behind it — a sale, a delivery, a
            breakage, a count that disagreed — so any figure on{" "}
            <Link className={styles.quiet} href="/stock">
              Stock
            </Link>{" "}
            can be walked back to what produced it.
          </p>
        </header>

        {!canSee ? (
          <section className={styles.panel}>
            <p className={styles.empty}>You cannot see stock.</p>
          </section>
        ) : movements.length === 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Nothing has moved yet. The first entry appears when you book a
              delivery in or a till takes a sale.
            </p>
          </section>
        ) : (
          <>
            {products.length > 1 || branches.length > 1 ? (
              <nav className={styles.filters} aria-label="Narrow the history">
                <Link
                  href="/stock/history"
                  className={`${styles.filter} ${
                    !product && !branch ? styles.filterOn : ""
                  }`}
                >
                  Everything
                </Link>
                {branches.map((b) => (
                  <Link
                    key={b}
                    href={`/stock/history?branch=${encodeURIComponent(b)}`}
                    className={`${styles.filter} ${
                      branch === b ? styles.filterOn : ""
                    }`}
                  >
                    {b}
                  </Link>
                ))}
                {products.map((p) => (
                  <Link
                    key={p}
                    href={`/stock/history?product=${encodeURIComponent(p)}`}
                    className={`${styles.filter} ${
                      product === p ? styles.filterOn : ""
                    }`}
                  >
                    {p}
                  </Link>
                ))}
              </nav>
            ) : null}

            <section className={styles.panel}>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>Product</th>
                      <th>What happened</th>
                      <th className={styles.num}>Change</th>
                      <th className={styles.num}>Left</th>
                    </tr>
                  </thead>
                  <tbody>
                    {movements.map((m) => {
                      const before = Number(m.quantity_before);
                      const after = Number(m.quantity_after);
                      const delta = after - before;
                      const kind = KIND[m.movement_type] ?? {
                        label: m.movement_type,
                        tone: "flat" as const,
                      };
                      return (
                        <tr key={m.id}>
                          <td>
                            <div className={styles.meta}>
                              {new Date(m.created_at).toLocaleString("en-KE")}
                            </div>
                          </td>
                          <td>
                            <div className={styles.name}>{m.product_name}</div>
                            <div className={styles.meta}>
                              {m.product_sku} · {m.branch_name}
                            </div>
                          </td>
                          <td>
                            <div>{kind.label}</div>
                            {/*
                              The reason a human typed, or the sale it came
                              from. This column is the whole reason the table
                              is worth reading — a movement with no account of
                              itself is just a number that changed.
                            */}
                            {m.reason || m.reference ? (
                              <div className={styles.meta}>
                                {m.reason || m.reference}
                              </div>
                            ) : null}
                          </td>
                          <td
                            className={`${styles.num} ${
                              delta > 0
                                ? styles.movedIn
                                : delta < 0
                                  ? styles.movedOut
                                  : ""
                            }`}
                          >
                            {delta > 0 ? "+" : ""}
                            {amount(String(delta))}
                          </td>
                          <td className={styles.num}>
                            {amount(m.quantity_after)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
