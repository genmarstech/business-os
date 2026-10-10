import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { MpesaForm, type Branch, type Till } from "./forms";
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

  /*
   * ── THE DEFAULT, AND ANY BRANCH THAT HAS ITS OWN ──────────────────────
   * The list used to be read as "the one till", `[0]`, which was true while
   * a business could only have one. A chain can now hold a default plus an
   * override per branch, and taking the first row would edit whichever the
   * database happened to return — possibly one shop's number while the
   * owner believed they were changing the business default.
   */
  const tills = rows(await getOrNull<Page<Till>>("/pay/mpesa/till/"));
  // The business default is the one this form edits; see the note above.
  const till = tills.find((t) => t.branch === null) ?? null;
  const overrides = tills.filter((t) => t.branch !== null);
  const branches = rows(
    await getOrNull<Page<Branch>>("/brn/branch/?is_active=true"),
  );
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

          <MpesaForm
            organizationId={organisationId}
            till={till}
            branches={branches}
          />
        </section>

        {/*
          Listed rather than hidden behind the form. An owner who set a
          branch's own number months ago needs to be able to SEE that it
          exists — otherwise the business default looks like the whole
          story, and a push from that shop goes somewhere they have
          forgotten about.
        */}
        {overrides.length > 0 ? (
          <section className={styles.panel}>
            <h2 className={styles.title}>Shops with their own number</h2>
            <ul className={styles.overrides}>
              {overrides.map((row) => (
                <li key={row.id} className={styles.override}>
                  <span className={styles.overrideWhere}>
                    {branches.find((b) => b.id === row.branch)?.branch_name ??
                      "A shop"}
                  </span>
                  <span className={styles.overrideCode}>
                    {row.short_code}
                    {row.store_number && row.store_number !== row.short_code
                      ? ` · store ${row.store_number}`
                      : ""}
                  </span>
                  <span className={styles.overrideState}>
                    {row.is_active ? row.environment : "off"}
                    {row.is_complete ? "" : " · incomplete"}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

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
