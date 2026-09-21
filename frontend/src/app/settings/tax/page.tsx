import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { catalogue } from "../../catalogue/data";
import { TaxRuleForm } from "../../catalogue/forms";
import styles from "../../catalogue/catalogue.module.css";

/**
 * Tax rules.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * A RATE CHANGED HERE IS NOT RETROACTIVE, AND THE SCREEN HAS TO SAY SO.
 *
 * Every sale copies the rate it charged onto its own line. Raising VAT today
 * leaves yesterday's receipts saying what they said — which is what a revenue
 * authority expects, and the opposite of what somebody editing one number here
 * would assume if nothing told them. Getting that assumption wrong in either
 * direction is a filing error, so it is stated on the screen and not only in
 * a comment.
 * ══════════════════════════════════════════════════════════════════════════
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Tax" };

export default async function TaxPage({
  searchParams,
}: {
  searchParams: Promise<{ edit?: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { edit } = await searchParams;
  const { taxRules, products } = await catalogue();
  const organisationId =
    me.kind === "subscriber" ? me.organisations[0]?.id : me.organisation.id;

  const allowed = may(me, PERM.settingsTax);
  const editing = edit ? taxRules.find((r) => r.id === Number(edit)) : undefined;

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Tax</h1>
          <p className={styles.sub}>
            The rates your prices are quoted against. A product points at one
            of these, and each sale copies the rate it was charged at — so
            changing a rate here decides what the next sale charges and
            rewrites nothing already rung up.
          </p>
        </header>

        {!allowed ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Only an owner or an admin changes tax rates.
            </p>
          </section>
        ) : (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>
                {taxRules.length === 0 ? "No rules yet" : "Your rules"}
              </h2>

              {taxRules.length === 0 ? (
                <p className={styles.empty}>
                  Without one, every price is treated as having no tax at all.
                  If you are registered for VAT, the standard rate in Kenya is
                  16%.
                </p>
              ) : (
                <div className={styles.scroll}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>Rule</th>
                        <th className={styles.num}>Rate</th>
                        <th>How it is charged</th>
                        <th className={styles.num}>Products</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {taxRules.map((rule) => (
                        <tr
                          key={rule.id}
                          className={rule.is_active ? "" : styles.withdrawn}
                        >
                          <td>
                            <div className={styles.name}>{rule.name}</div>
                            {rule.is_default ? (
                              <div className={styles.meta}>
                                Used for new products
                              </div>
                            ) : null}
                          </td>
                          <td className={styles.num}>{rule.rate}%</td>
                          <td>
                            {rule.is_inclusive
                              ? "Included in the shelf price"
                              : "Added at the till"}
                          </td>
                          <td className={styles.num}>
                            {
                              products.filter((p) => p.tax_rule === rule.id)
                                .length
                            }
                          </td>
                          <td className={styles.num}>
                            <Link
                              className={styles.quiet}
                              href={`/settings/tax?edit=${rule.id}#rule`}
                            >
                              Edit
                            </Link>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {organisationId ? (
              <section className={styles.panel} id="rule">
                <h2 className={styles.panelTitle}>
                  {editing ? `Edit ${editing.name}` : "Add a rule"}
                </h2>
                {editing ? (
                  <div className={styles.note}>
                    <strong>
                      This changes what future sales charge, and nothing else.
                    </strong>{" "}
                    The{" "}
                    {products.filter((p) => p.tax_rule === editing.id).length}{" "}
                    product
                    {products.filter((p) => p.tax_rule === editing.id).length ===
                    1
                      ? ""
                      : "s"}{" "}
                    using it will be quoted at the new rate from the moment you
                    save. Every receipt already issued keeps the rate it was
                    charged at, which is what your returns are filed against.
                  </div>
                ) : null}

                <TaxRuleForm
                  key={editing?.id ?? "new"}
                  organisationId={organisationId}
                  rule={editing}
                />
              </section>
            ) : null}
          </>
        )}
      </div>
    </Shell>
  );
}
