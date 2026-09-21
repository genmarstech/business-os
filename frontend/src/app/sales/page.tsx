import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { ksh } from "@/lib/reports";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { RefundForm, ReprintForm, VoidForm } from "./forms";
import type { Sale } from "./shape";
import styles from "./sales.module.css";

/**
 * What has been sold, and the two ways to undo it.
 *
 * ── THE HISTORY IS IMMUTABLE, SO THIS SCREEN NEVER EDITS A SALE ────────────
 * Blueprint §10. A void marks a sale voided and puts the stock back; a refund
 * writes its own row beside the original. Neither alters what was charged, so
 * the figures a shop files against stay what they were — and the screen shows
 * both states rather than making a cancelled sale disappear, because a sale
 * somebody remembers taking and cannot find is worse than one marked void.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Sales" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function SalesPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [salePage, staffPage] = await Promise.all([
    getOrNull<Page<Sale>>("/sls/sales/"),
    getOrNull<Page<{ id: number; full_name: string }>>("/org/staff/"),
  ]);

  const sales = rows(salePage);
  const staff = rows(staffPage);

  /*
   * A refund is recorded against a member of staff, and the backend pins that
   * to the signed-in principal when the caller is a till. A subscriber has no
   * staff record of their own, so the first employee on the books stands in —
   * and the screen says so rather than recording it silently.
   */
  const staffId = me.kind === "staff" ? me.staff_id : (staff[0]?.id ?? null);
  const standIn = me.kind === "subscriber" ? (staff[0]?.full_name ?? null) : null;

  const canVoid = may(me, PERM.salesVoid);
  const canRefund = may(me, PERM.salesRefund);
  const canReprint = may(me, PERM.salesReprint);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Trading</p>
          <h1 className={styles.title}>Sales</h1>
          <p className={styles.sub}>
            Every sale as it was rung up. Nothing here edits one — cancelling
            marks it cancelled and puts the stock back, refunding writes a
            refund beside it, and what was charged stays what was charged.
          </p>
        </header>

        {sales.length === 0 ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Nothing has been sold yet. Sales appear here the moment a till
              takes one.
            </p>
          </section>
        ) : (
          sales.map((sale) => (
            <article key={sale.id} className={styles.sale}>
              <header className={styles.saleHead}>
                <div>
                  <div className={styles.number}>{sale.number}</div>
                  <div className={styles.meta}>
                    {new Date(
                      sale.completed_at ?? sale.created_at,
                    ).toLocaleString("en-KE")}
                  </div>
                </div>
                <div className={styles.saleRight}>
                  <Badge sale={sale} />
                  <div className={styles.saleTotal}>{ksh(sale.total)}</div>
                </div>
              </header>

              <table className={styles.table}>
                <tbody>
                  {sale.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <span className={styles.name}>{item.product_name}</span>
                        <span className={styles.meta}> {item.sku}</span>
                      </td>
                      <td className={styles.num}>× {Number(item.quantity)}</td>
                      <td className={styles.num}>{ksh(item.unit_price)}</td>
                      <td className={styles.num}>{ksh(item.line_total)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={3}>Tax</td>
                    <td className={styles.num}>{ksh(sale.tax_total)}</td>
                  </tr>
                  {sale.payments.map((payment) => (
                    <tr key={payment.id}>
                      <td colSpan={3}>
                        Paid — {payment.method_label ?? payment.method}
                        {payment.reference ? ` · ${payment.reference}` : ""}
                      </td>
                      <td className={styles.num}>{ksh(payment.amount)}</td>
                    </tr>
                  ))}
                  {Number(sale.amount_refunded) > 0 ? (
                    <tr>
                      <td colSpan={3} className={styles.refunded}>
                        Refunded
                      </td>
                      <td className={`${styles.num} ${styles.refunded}`}>
                        −{ksh(sale.amount_refunded)}
                      </td>
                    </tr>
                  ) : null}
                </tfoot>
              </table>

              {sale.status === "voided" ? (
                <p className={styles.voided}>
                  Cancelled{sale.void_reason ? ` — ${sale.void_reason}` : ""}.
                  The stock went back and it is not counted in any total.
                </p>
              ) : (
                <div className={styles.actions}>
                  {canRefund ? (
                    <RefundForm sale={sale} staffId={staffId} />
                  ) : null}
                  {canReprint ? <ReprintForm sale={sale} /> : null}
                  {canVoid ? <VoidForm sale={sale} /> : null}
                </div>
              )}

              {standIn && (canRefund || canVoid) && sale.status !== "voided" ? (
                <p className={styles.footnote}>
                  A refund from this screen is recorded against {standIn}. A
                  cashier refunding at their own till is recorded against
                  themselves.
                </p>
              ) : null}
            </article>
          ))
        )}
      </div>
    </Shell>
  );
}

/**
 * The state of a sale, as a shape as well as a word.
 *
 * Colour alone would not survive a printed page or a colour-blind reader, so
 * the label carries the whole meaning and the colour only speeds it up.
 */
function Badge({ sale }: { sale: Sale }) {
  const refunded = Number(sale.amount_refunded) > 0;
  const tone =
    sale.status === "voided"
      ? styles.badgeVoid
      : refunded
        ? styles.badgeWarn
        : styles.badgeOk;

  const label =
    sale.status === "voided"
      ? "Cancelled"
      : refunded
        ? Number(sale.amount_refunded) >= Number(sale.total)
          ? "Fully refunded"
          : "Part refunded"
        : sale.status_label;

  return <span className={`${styles.badge} ${tone}`}>{label}</span>;
}
