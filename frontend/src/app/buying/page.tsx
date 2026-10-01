import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { amount, ksh } from "@/lib/money";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { OrderActions, RaiseOrderForm } from "./forms";
import { OPEN, type Branch, type Order, type Product, type Supplier } from "./types";
import styles from "./buying.module.css";

/**
 * What the business has asked for, and what has turned up.
 *
 * ── THE SCREEN IS ORGANISED BY WHAT IS STILL OWED ─────────────────────────
 * Open orders first, closed ones after, because the question somebody has
 * here is "what are we waiting for". "What did we buy in March, and from
 * whom" is a different question and lives on /buying/reports.
 * here is "what are we waiting for" — not "what did we buy in March", which
 * is a report and does not exist yet.
 *
 * ── AND IT NEVER SHOWS A BUTTON THE SERVER WILL REFUSE ────────────────────
 * Each action is asked for AT THE ORDER'S BRANCH. One person can be a
 * purchasing officer at Westlands and nothing at Karen, and `permissions` on
 * its own is the union — it would offer Approve at a branch where it will be
 * denied. None of this is security; the endpoint checks again.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Buying" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

function badgeClass(status: Order["status"]): string {
  if (status === "submitted") return styles.waitingBadge ?? "";
  if (status === "received") return styles.doneBadge ?? "";
  return "";
}

function when(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default async function BuyingPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [orderPage, supplierPage, branchPage, productPage] = await Promise.all([
    getOrNull<Page<Order>>("/prc/purchase-orders/"),
    getOrNull<Page<Supplier>>("/prc/suppliers/"),
    getOrNull<Page<Branch>>("/brn/branch/"),
    getOrNull<Page<Product>>("/ctl/products/"),
  ]);

  const orders = rows(orderPage);
  const suppliers = rows(supplierPage).filter((s) => s.is_active);
  const products = rows(productPage).filter((p) => p.is_active);

  // Only branches this caller may actually raise an order for. Offering the
  // rest would be offering a refusal.
  const branches = rows(branchPage).filter(
    (branch) => branch.is_active && may(me, PERM.purchasingManage, branch.id),
  );

  const open = orders.filter((order) => OPEN.includes(order.status));
  const closed = orders.filter((order) => !OPEN.includes(order.status));
  const canRaise = branches.length > 0 && suppliers.length > 0 && products.length > 0;

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Buying</p>
          <h1 className={styles.title}>Purchase orders</h1>
          <p className={styles.sub}>
            What has been asked for, from whom, and how much of it has
            arrived. Stock only ever goes up here by being counted in against
            an order — so every unit on a shelf can be walked back to the
            delivery that brought it and the supplier it came from.
          </p>
        </header>

        {suppliers.length === 0 && may(me, PERM.purchasingManage) ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>No suppliers yet</h2>
            <p className={styles.panelLede}>
              An order is addressed to somebody. Add the businesses you buy
              from on the Suppliers screen, then come back.
            </p>
          </section>
        ) : null}

        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>
            {open.length === 0
              ? "Nothing on order"
              : `${open.length} order${open.length === 1 ? "" : "s"} open`}
          </h2>
          {open.length === 0 ? (
            <p className={styles.empty}>
              Nothing is outstanding with a supplier.
            </p>
          ) : null}
        </section>

        {open.map((order) => (
          <OrderCard key={order.id} order={order} me={me} />
        ))}

        {canRaise ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Raise an order</h2>
            <p className={styles.panelLede}>
              It is written as a draft, which is the only state it can still
              be edited in. Sending it is what makes it a document the
              supplier has been given.
            </p>
            <RaiseOrderForm
              branches={branches}
              suppliers={suppliers}
              products={products}
            />
          </section>
        ) : null}

        {closed.length > 0 ? (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Closed</h2>
              <p className={styles.empty}>
                Received in full, or cancelled. Kept because an order explains
                where stock came from long after it has been sold.
              </p>
            </section>
            {closed.map((order) => (
              <OrderCard key={order.id} order={order} me={me} closed />
            ))}
          </>
        ) : null}
      </div>
    </Shell>
  );
}

function OrderCard({
  order,
  me,
  closed,
}: {
  order: Order;
  me: NonNullable<Awaited<ReturnType<typeof whoAmI>>>;
  closed?: boolean;
}) {
  const received = order.items.some((line) => Number(line.quantity_received) > 0);

  return (
    <section
      className={`${styles.panel} ${closed ? styles.closed : ""}`}
      aria-label={`Order ${order.number}`}
    >
      <div className={styles.orderHead}>
        <div>
          <h2 className={styles.panelTitle}>{order.supplier_name}</h2>
          <div className={styles.orderNumber}>
            Order #{order.number} · {order.branch_name}
          </div>
        </div>
        <div>
          <span className={`${styles.badge} ${badgeClass(order.status)}`}>
            {order.status_label}
          </span>
        </div>
      </div>

      <ul className={styles.facts}>
        <li className={styles.orderTotal}>KSh {ksh(order.total)}</li>
        {order.expected_at ? <li>Expected {when(order.expected_at)}</li> : null}
        {order.raised_by_name ? <li>Raised by {order.raised_by_name}</li> : null}
        {order.approved_by_name ? (
          <li>Approved by {order.approved_by_name}</li>
        ) : null}
        {order.cancelled_at ? (
          <li>
            Cancelled{order.cancelled_reason ? ` — ${order.cancelled_reason}` : ""}
          </li>
        ) : null}
      </ul>

      {order.note ? <p className={styles.meta}>{order.note}</p> : null}

      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Product</th>
              <th className={styles.num}>Ordered</th>
              {received ? <th className={styles.num}>Received</th> : null}
              <th className={styles.num}>Each</th>
              <th className={styles.num}>Line</th>
            </tr>
          </thead>
          <tbody>
            {order.items.map((line) => {
              const short = Number(line.outstanding) > 0 && received;
              return (
                <tr key={line.id}>
                  <td>
                    <div className={styles.name}>{line.product_name}</div>
                    <div className={styles.meta}>{line.product_sku}</div>
                  </td>
                  <td className={styles.num}>{amount(line.quantity_ordered)}</td>
                  {received ? (
                    <td className={`${styles.num} ${short ? styles.short : ""}`}>
                      {amount(line.quantity_received)}
                      {short ? (
                        <div className={styles.meta}>
                          {amount(line.outstanding)} to come
                        </div>
                      ) : null}
                    </td>
                  ) : null}
                  <td className={styles.num}>{ksh(line.unit_cost)}</td>
                  <td className={styles.num}>{ksh(line.line_total)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <OrderActions
        order={order}
        canSend={may(me, PERM.purchasingManage, order.branch)}
        canDecide={may(me, PERM.purchasingApprove, order.branch)}
        canReceive={may(me, PERM.purchasingReceive, order.branch)}
      />
    </section>
  );
}
