import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { BusinessForm } from "./forms";
import styles from "../../branches/branches.module.css";

/**
 * The business itself.
 *
 * ── OWNER ONLY, AND SEPARATELY FROM EVERY OTHER SETTING ────────────────────
 * settings.organisation is its own permission, held by an owner and not by an
 * admin — see identity/access.py, where _ADMIN is everything MINUS this and
 * staff.manage. Who the business IS, and who may work its tills, are the two
 * things an administrator does not get to decide.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Business details" };

type Organisation = {
  id: number;
  name: string;
  staff_size?: string;
  staff_size_display?: string;
  org_number?: string;
};

type Page<T> = { results?: T[] } | T[];

export default async function BusinessPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const page = await getOrNull<Page<Organisation>>("/org/organizations/");
  const list = !page ? [] : Array.isArray(page) ? page : (page.results ?? []);
  const organisation = list[0];
  const allowed = may(me, PERM.settingsOrganisation);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Business details</h1>
          <p className={styles.sub}>
            Who this business is. Its number is ours and never changes — the
            name is a label, so two shops may share one and changing yours
            affects nobody.
          </p>
        </header>

        {!organisation ? (
          <section className={styles.panel}>
            <p className={styles.empty}>No business to show.</p>
          </section>
        ) : !allowed ? (
          <section className={styles.panel}>
            <div className={styles.card}>
              <div className={styles.name}>{organisation.name}</div>
              <div className={styles.meta}>
                {organisation.org_number ? `${organisation.org_number} · ` : ""}
                {organisation.staff_size_display ?? ""}
              </div>
            </div>
            <p className={`${styles.empty} ${styles.emptyBelow}`}>
              Only an owner changes these. An administrator runs everything
              else.
            </p>
          </section>
        ) : (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>The number a till asks for</h2>
              <p className={styles.panelLede}>
                A cashier types this once, the first time they sign in at a
                terminal, and it is remembered after that. It is not a secret
                — without a username and password for this business it opens
                nothing.
              </p>

              {/*
                ── THE ID, NOT org_number ─────────────────────────────────
                /auth/staff/sign-in takes the organisation's primary key.
                org_number is a reference for correspondence and a till will
                not accept it. Showing the prettier one large, with the real
                one in small print underneath, is how somebody ends up typing
                the wrong thing at a counter — so the useful number leads.
              */}
              <div className={styles.bigNumber}>{organisation.id}</div>

              {organisation.org_number ? (
                <p className={styles.meta}>
                  Your reference with us is {organisation.org_number}. Quote it
                  to Genmars; a till will not take it.
                </p>
              ) : null}
            </section>

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Name and size</h2>
              <BusinessForm organisation={organisation} />
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
