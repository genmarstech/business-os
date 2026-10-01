import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { AddSupplierForm, ArchiveSupplier } from "../forms";
import type { Supplier } from "../types";
import styles from "../buying.module.css";

/**
 * Who the business buys from.
 *
 * ── THEY ARE NEVER DELETED ────────────────────────────────────────────────
 * A supplier with orders behind it is PROTECTed at the database, and an
 * archived one still explains every order that names it. Archiving takes it
 * out of the list you can order from and changes nothing that already
 * happened — the same shape as a closed branch, for the same reason.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Suppliers" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function SuppliersPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const suppliers = rows(await getOrNull<Page<Supplier>>("/prc/suppliers/"));
  const canManage = may(me, PERM.purchasingManage);

  /*
   * Which business this belongs to. A till session has exactly one; a
   * subscriber may belong to several and the first is taken, which is correct
   * until there is a tenant switcher — the same thing Shell does for the
   * name in the sidebar.
   */
  const organisationId =
    me.kind === "staff" ? me.organisation.id : me.organisations[0]?.id;

  const active = suppliers.filter((supplier) => supplier.is_active);
  const archived = suppliers.filter((supplier) => !supplier.is_active);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Buying</p>
          <h1 className={styles.title}>Suppliers</h1>
          <p className={styles.sub}>
            The businesses this shop buys from. Each one belongs to you alone
            — two shops on the platform both buying from Brookside keep two
            separate records, because one shop editing a phone number has no
            business changing another&rsquo;s.
          </p>
        </header>

        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>
            {active.length === 0
              ? "No suppliers yet"
              : `${active.length} supplier${active.length === 1 ? "" : "s"}`}
          </h2>
          {active.length === 0 ? (
            <p className={styles.empty}>
              Add the first one below, then raise an order against it.
            </p>
          ) : (
            <ul className={styles.cards}>
              {active.map((supplier) => (
                <Card
                  key={supplier.id}
                  supplier={supplier}
                  canManage={canManage}
                />
              ))}
            </ul>
          )}
        </section>

        {canManage && organisationId ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Add a supplier</h2>
            <p className={styles.panelLede}>
              Only the name is required. Everything else is there because
              somebody standing at a goods door needs a number to ring.
            </p>
            <AddSupplierForm organisationId={organisationId} />
          </section>
        ) : null}

        {archived.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.panelTitle}>Archived</h2>
            <p className={styles.panelLede}>
              Not offered when raising an order. Still named on every order
              they ever supplied.
            </p>
            <ul className={styles.cards}>
              {archived.map((supplier) => (
                <Card
                  key={supplier.id}
                  supplier={supplier}
                  canManage={canManage}
                />
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </Shell>
  );
}

function Card({
  supplier,
  canManage,
}: {
  supplier: Supplier;
  canManage: boolean;
}) {
  return (
    <li className={`${styles.card} ${supplier.is_active ? "" : styles.closed}`}>
      <div className={styles.cardHead}>
        <span className={styles.name}>{supplier.name}</span>
        {supplier.lead_time_days > 0 ? (
          <span className={styles.badge}>
            {supplier.lead_time_days} day
            {supplier.lead_time_days === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>

      {supplier.contact_person ? (
        <span className={styles.meta}>{supplier.contact_person}</span>
      ) : null}
      {supplier.phone_number ? (
        <span className={styles.meta}>{supplier.phone_number}</span>
      ) : null}
      {supplier.email ? (
        <span className={styles.meta}>{supplier.email}</span>
      ) : null}
      {supplier.address ? (
        <span className={styles.meta}>{supplier.address}</span>
      ) : null}
      {supplier.note ? <span className={styles.meta}>{supplier.note}</span> : null}

      {canManage ? (
        <ArchiveSupplier
          supplierId={supplier.id}
          active={supplier.is_active}
        />
      ) : null}
    </li>
  );
}
