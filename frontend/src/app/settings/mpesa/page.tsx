import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { MpesaForm, type Till } from "./forms";
import styles from "./mpesa.module.css";

/**
 * The shop's own M-Pesa till.
 *
 * ── THIS IS THE SHOP BEING PAID, NOT THE SHOP PAYING GENMARS ──────────────
 * A customer pays the SHOP, on the shop's own paybill, using the shop's own
 * Daraja application. No money passes through Genmars and no Genmars
 * credential is involved. What the shop pays Genmars is under Subscription,
 * and the two are deliberately unconnected.
 *
 * ── OWNER ONLY ────────────────────────────────────────────────────────────
 * Entering a merchant credential is the owner's act. An administrator does
 * not get it and a till certainly does not — a cashier needs M-Pesa to work
 * and has no business reading how it is wired.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "M-Pesa" };

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function MpesaPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  if (!may(me, PERM.settingsOrganisation)) {
    return (
      <Shell me={me}>
        <div className={styles.page}>
          <header className={styles.head}>
            <p className={styles.eyebrow}>Settings</p>
            <h1 className={styles.title}>M-Pesa</h1>
          </header>
          <section className={styles.panel}>
            <p className={styles.empty}>
              How M-Pesa is wired up is the owner&rsquo;s to see. You can still
              take M-Pesa payments at the till if it is switched on.
            </p>
          </section>
        </div>
      </Shell>
    );
  }

  const till = rows(await getOrNull<Page<Till>>("/pay/mpesa/till/"))[0] ?? null;
  const organisationId =
    me.kind === "staff" ? me.organisation.id : (me.organisations[0]?.id ?? 0);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Settings</p>
          <h1 className={styles.title}>M-Pesa</h1>
          <p className={styles.sub}>
            Let a cashier send a payment request straight to a
            customer&rsquo;s phone. The money goes to your own paybill, and
            the sale rings itself up the moment Safaricom confirms it.
          </p>
        </header>

        <section className={styles.panel}>
          <div className={styles.status}>
            <span
              className={`${styles.chip} ${
                till?.is_active ? styles.chipOn : styles.chipOff
              }`}
            >
              {till?.is_active ? "On at the till" : "Off"}
            </span>
            {till?.environment === "production" ? (
              <span className={`${styles.chip} ${styles.chipLive}`}>
                Real money
              </span>
            ) : (
              <span className={`${styles.chip} ${styles.chipOff}`}>Sandbox</span>
            )}
            {till && !till.is_complete ? (
              <span className={`${styles.chip} ${styles.chipLive}`}>
                Incomplete
              </span>
            ) : null}
          </div>

          <MpesaForm organizationId={organisationId} till={till} />
        </section>

        <p className={styles.footnote}>
          Your credentials are encrypted before they are stored and are never
          shown again — not here, not in an export, not to Genmars support.
          Nothing in this product can move money out of your M-Pesa account;
          it can only ask a customer to pay into it.
        </p>
      </div>
    </Shell>
  );
}
