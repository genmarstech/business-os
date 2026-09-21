import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { amount } from "@/lib/reports";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { AdjustForm, StockAProductForm } from "./forms";
import styles from "./stock.module.css";

/**
 * What is on the shelves, by branch.
 *
 * ── STOCK IS PER BRANCH, AND THAT IS THE WHOLE POINT ───────────────────────
 * "We have 40" is not an answer a chain can act on. The table is grouped by
 * branch because the question somebody actually has is whether THIS shop can
 * sell the thing in front of them.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Stock" };

type Row = {
  id: number;
  branch: number;
  branch_name: string;
  product: number;
  product_name: string;
  product_sku: string;
  quantity: string;
  reorder_level: string;
  is_active: boolean;
};

type Branch = { id: number; branch_name: string; is_active: boolean };
type Product = { id: number; name: string; sku: string; is_active: boolean };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function StockPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [stockPage, branchPage, productPage] = await Promise.all([
    getOrNull<Page<Row>>("/invt/inventory/"),
    getOrNull<Page<Branch>>("/brn/branch/"),
    getOrNull<Page<Product>>("/ctl/products/"),
  ]);

  const stock = rows(stockPage);
  const branches = rows(branchPage).filter((b) => b.is_active);
  const products = rows(productPage).filter((p) => p.is_active);
  const canAdjust = may(me, PERM.inventoryAdjust);

  const byBranch = new Map<string, Row[]>();
  for (const row of stock) {
    const key = row.branch_name || "Unassigned";
    byBranch.set(key, [...(byBranch.get(key) ?? []), row]);
  }

  /*
   * A product in the catalogue with no row at any branch cannot be sold
   * anywhere, and nothing else in the product says so. Worth surfacing, since
   * the usual way to reach that state is adding a product and forgetting this
   * screen exists.
   */
  const stocked = new Set(stock.map((row) => row.product));
  const unstocked = products.filter((p) => !stocked.has(p.id));

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Inventory</p>
          <h1 className={styles.title}>Stock</h1>
          <p className={styles.sub}>
            What each branch holds. A quantity here only ever changes by an
            amount with a reason attached — a sale, a delivery, a breakage —
            so any figure can be traced back to what moved it.
          </p>
        </header>

        {unstocked.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>
              {unstocked.length} product
              {unstocked.length === 1 ? "" : "s"} on no shelf
            </h2>
            <p className={styles.panelLede}>
              In the catalogue but not stocked at any branch, so no till can
              sell {unstocked.length === 1 ? "it" : "them"}.
            </p>
            <ul className={styles.chips}>
              {unstocked.map((product) => (
                <li key={product.id} className={styles.chip}>
                  {product.name}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {stock.length === 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Nothing is stocked yet. Put a product on a branch&rsquo;s shelf
              below, then book the first delivery in.
            </p>
          </section>
        ) : (
          [...byBranch.entries()].map(([branch, lines]) => (
            <section key={branch} className={styles.panel}>
              <h2 className={styles.panelTitle}>{branch}</h2>
              <div className={styles.scroll}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th className={styles.num}>On hand</th>
                      <th className={styles.num}>Low at</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => {
                      const low =
                        Number(line.reorder_level) > 0 &&
                        Number(line.quantity) <= Number(line.reorder_level);
                      const out = Number(line.quantity) <= 0;
                      return (
                        <tr key={line.id}>
                          <td>
                            <div className={styles.name}>
                              {line.product_name}
                            </div>
                            <div className={styles.meta}>
                              {line.product_sku}
                            </div>
                          </td>
                          <td
                            className={`${styles.num} ${
                              out ? styles.outOf : low ? styles.low : ""
                            }`}
                          >
                            {amount(line.quantity)}
                            {out ? (
                              <div className={styles.meta}>
                                Cannot be sold
                              </div>
                            ) : low ? (
                              <div className={styles.meta}>Running low</div>
                            ) : null}
                          </td>
                          <td className={styles.num}>
                            {Number(line.reorder_level) > 0
                              ? amount(line.reorder_level)
                              : "—"}
                          </td>
                          <td className={styles.right}>
                            {canAdjust ? (
                              <AdjustForm
                                inventoryId={line.id}
                                productName={line.product_name}
                                onHand={amount(line.quantity)}
                              />
                            ) : null}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          ))
        )}

        {canAdjust && branches.length > 0 && products.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Stock a product at a branch</h2>
            <p className={styles.panelLede}>
              A product in the catalogue is not yet stock anywhere. This puts
              it on one branch&rsquo;s shelf at zero, ready for its first
              delivery to be booked in.
            </p>
            <StockAProductForm branches={branches} products={products} />
          </section>
        ) : null}
      </div>
    </Shell>
  );
}
