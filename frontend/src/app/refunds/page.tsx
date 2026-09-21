import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { ksh } from "@/lib/reports";
import { me as whoAmI } from "@/lib/session";
import styles from "./refunds.module.css";

/**
 * Money that went back.
 *
 * ── A REFUND IS ITS OWN RECORD, NOT A HOLE IN A SALE ───────────────────────
 * Blueprint §10. The original sale still says what was charged; this is the
 * separate row that says what was returned, when, by whom and why. The reason
 * is the column a manager actually reads — a day of refunds all saying "wrong
 * size" is a supplier problem, and all saying nothing is a different one.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Refunds" };

type Refund = {
  id: number;
  number: number;
  sale: number;
  sale_number: number;
  branch: number;
  processed_by: number;
  total: string;
  reason: string;
  created_at: string;
  items: {
    id: number;
    product_name: string;
    quantity: string;
    amount: string;
    restocked: boolean;
  }[];
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function RefundsPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const refunds = rows(await getOrNull<Page<Refund>>("/sls/refunds/"));

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Trading</p>
          <h1 className={styles.title}>Refunds</h1>
          <p className={styles.sub}>
            Every refund given, against the sale it came from. Refunding is
            done from{" "}
            <Link className={styles.quiet} href="/sales">
              the sale itself
            </Link>{" "}
            — this is the record of what has been given back.
          </p>
        </header>

        {refunds.length === 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Nothing has been refunded. That is worth knowing on its own.
            </p>
          </section>
        ) : (
          <section className={styles.panel}>
            <div className={styles.scroll}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Refund</th>
                    <th>What came back</th>
                    <th>Why</th>
                    <th className={styles.num}>Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {refunds.map((refund) => (
                    <tr key={refund.id}>
                      <td>
                        <div className={styles.number}>{refund.number}</div>
                        <div className={styles.meta}>
                          against sale {refund.sale_number}
                        </div>
                        <div className={styles.meta}>
                          {new Date(refund.created_at).toLocaleString("en-KE")}
                        </div>
                      </td>
                      <td>
                        <ul className={styles.tight}>
                          {refund.items.map((item) => (
                            <li key={item.id}>
                              {Number(item.quantity)} × {item.product_name}
                              {item.restocked ? null : (
                                <span className={styles.warn}>
                                  {" "}
                                  — not restocked
                                </span>
                              )}
                            </li>
                          ))}
                        </ul>
                      </td>
                      <td className={styles.reason}>{refund.reason}</td>
                      <td className={styles.num}>{ksh(refund.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={styles.footnote}>
              &ldquo;Not restocked&rdquo; means the item did not go back on the
              shelf — damaged, or returned to a supplier. Counting those back
              in would be stock the shop does not have.
            </p>
          </section>
        )}
      </div>
    </Shell>
  );
}
