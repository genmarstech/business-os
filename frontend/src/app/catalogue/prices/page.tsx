import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { ksh } from "@/lib/money";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { NewPriceList, ToggleList } from "./forms";
import type { Branch, PriceList, Product } from "./types";
import styles from "./prices.module.css";

/**
 * Promotions, branch pricing and wholesale rates.
 *
 * ── THE STATE OF A LIST READS BEFORE ANYTHING ELSE ────────────────────────
 * The mistake this screen exists to catch is a promotion somebody believes
 * is running and is not — the dates were typed wrong, or it was never turned
 * on. So every list carries a chip saying Running, Waiting or Off, and
 * "waiting" is deliberately the loud one: an inactive list looks off and is,
 * while an active list outside its dates looks fine and does nothing.
 *
 * ── THE BASE PRICE IS SHOWN BESIDE EVERY OVERRIDE ─────────────────────────
 * An override on its own is a number with no meaning. 80 is a promotion
 * against 100 and a mistake against 60, and the person checking cannot tell
 * which without the thing it replaced.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Price lists" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

function when(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Running, waiting or off — three states, because two would hide the middle. */
function status(list: PriceList): { text: string; className: string } {
  if (!list.is_active) {
    return { text: "Off", className: styles.chipOff ?? "" };
  }
  if (!list.in_force) {
    return { text: "Not running yet", className: styles.chipWaiting ?? "" };
  }
  return { text: "Running", className: styles.chipLive ?? "" };
}

function window(list: PriceList): string {
  if (!list.starts_on && !list.ends_on) return "No dates";
  if (list.starts_on && list.ends_on) {
    return `${when(list.starts_on)} – ${when(list.ends_on)}`;
  }
  if (list.starts_on) return `From ${when(list.starts_on)}`;
  return `Until ${when(list.ends_on)}`;
}

export default async function PricesPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const canManage = may(me, PERM.catalogManage);

  const [listPage, branchPage, productPage] = await Promise.all([
    getOrNull<Page<PriceList>>("/ctl/price-lists/"),
    getOrNull<Page<Branch>>("/brn/branch/"),
    getOrNull<Page<Product>>("/ctl/products/"),
  ]);

  const lists = rows(listPage);
  const branches = rows(branchPage).filter((b) => b.is_active);
  const products = rows(productPage).filter((p) => p.is_active);

  const organisationId =
    me.kind === "staff" ? me.organisation.id : (me.organisations[0]?.id ?? 0);

  const byBranch = new Map(branches.map((b) => [b.id, b.branch_name]));

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <div>
            <p className={styles.eyebrow}>Catalogue</p>
            <h1 className={styles.title}>Price lists</h1>
            <p className={styles.sub}>
              Charge something other than the usual price — at one branch, for
              a fortnight, or both. Anything a list does not mention keeps its
              usual price.
            </p>
          </div>
          {canManage && organisationId ? (
            <NewPriceList
              organizationId={organisationId}
              branches={branches}
              products={products}
            />
          ) : null}
        </header>

        {lists.length === 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              No price lists. Every product sells at the price on its
              catalogue entry, which is the right answer for most shops.
            </p>
          </section>
        ) : (
          lists.map((list) => {
            const state = status(list);
            return (
              <section key={list.id} className={styles.panel}>
                <div className={styles.listHead}>
                  <div>
                    <h2 className={styles.panelTitle}>
                      {list.name}{" "}
                      <span className={`${styles.chip} ${state.className}`}>
                        {state.text}
                      </span>
                    </h2>
                    <div className={styles.listMeta}>
                      <span>{window(list)}</span>
                      <span>
                        {list.branches.length === 0
                          ? "Every branch"
                          : list.branches
                              .map((id) => byBranch.get(id) ?? `#${id}`)
                              .join(", ")}
                      </span>
                      <span className={styles.rank}>
                        Precedence {list.precedence}
                      </span>
                    </div>
                  </div>
                  {canManage ? (
                    <ToggleList id={list.id} isActive={list.is_active} />
                  ) : null}
                </div>

                {list.entries.length === 0 ? (
                  <p className={styles.empty}>
                    No prices on it yet, so it changes nothing.
                  </p>
                ) : (
                  <div className={styles.scroll}>
                    <table className={styles.table}>
                      <thead>
                        <tr>
                          <th>Product</th>
                          <th className={styles.num}>Usually</th>
                          <th className={styles.num}>On this list</th>
                        </tr>
                      </thead>
                      <tbody>
                        {list.entries.map((entry) => (
                          <tr key={entry.id}>
                            <td>
                              <div className={styles.name}>
                                {entry.product_name}
                              </div>
                              <div className={styles.meta}>
                                {entry.product_sku}
                              </div>
                            </td>
                            <td className={`${styles.num} ${styles.struck}`}>
                              {ksh(entry.base_price)}
                            </td>
                            <td className={styles.num}>{ksh(entry.price)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            );
          })
        )}

        <p className={styles.footnote}>
          Where two lists both apply, the higher precedence wins — and no two
          lists in a business may share a number, so there is always exactly
          one answer. Changing a list changes tomorrow&rsquo;s prices and
          rewrites nothing: what a sale charged was copied onto it at the
          time.
        </p>
      </div>
    </Shell>
  );
}
