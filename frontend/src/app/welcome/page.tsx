import Link from "next/link";
import { redirect } from "next/navigation";

import { Mark } from "@/components/Mark";
import { progress, type Step } from "@/lib/onboarding";
import { me as whoAmI, tenantName } from "@/lib/session";
import { BranchForm, BusinessForm, ProductForm, RegisterForm } from "./forms";
import styles from "./welcome.module.css";

/**
 * Onboarding.
 *
 * ── ONE ROUTE, NOT FOUR ────────────────────────────────────────────────────
 *
 * /welcome/business, /welcome/branch and so on would each need to decide
 * whether the caller belongs there and bounce them if not — four places to
 * get that wrong, and a back button that lands somebody on a step they have
 * already done. Here the URL means "wherever I am", the step is derived from
 * what exists (see lib/onboarding.ts), and refreshing after any action lands
 * on the next thing.
 *
 * ── IT IS NOT A CAGE ───────────────────────────────────────────────────────
 *
 * Nothing forces a subscriber through this. The dashboard is reachable
 * throughout, the API takes these creates in any order, and somebody who
 * would rather add ten products before their second branch is not stopped.
 * This is the shortest path to a working till, offered — not a gate.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Set up your business" };

const RUNGS: { step: Step; name: string }[] = [
  { step: "business", name: "Your business" },
  { step: "branch", name: "A branch" },
  { step: "catalogue", name: "Something to sell" },
  { step: "register", name: "A till" },
];

export default async function Welcome() {
  const me = await whoAmI();
  if (!me) redirect("/");

  /*
   * A till has no business here. Operational staff are created BY a
   * subscriber — they cannot create the organisation that employs them, and
   * every form below would be refused. Sending them to the dashboard is more
   * useful than showing them four forms they cannot submit.
   */
  if (me.kind === "staff") redirect("/");

  const state = await progress(me);
  const organisationId = me.organisations[0]?.id ?? null;
  const business = tenantName(me);

  const reached = RUNGS.findIndex((r) => r.step === state.next);
  const position = reached === -1 ? RUNGS.length : reached;

  return (
    <div className={styles.page}>
      <div className={styles.brand}>
        <Mark size={30} />
        <div>
          <div className={styles.brandName}>{business ?? "Genmars"}</div>
          <div className={styles.brandSub}>
            {business ? "Genmars" : "Business Platform"}
          </div>
        </div>
      </div>

      <h1 className={styles.title}>
        {state.next === "done"
          ? "You are ready to sell"
          : "Let us get you selling"}
      </h1>
      <p className={styles.lede}>
        Four things have to exist before a till can take a sale, and each one
        needs the one before it. You can leave and come back — this page picks
        up wherever you got to.
      </p>

      <ol className={styles.rail}>
        {RUNGS.map((rung, index) => {
          const done = index < position;
          const here = index === position;
          return (
            <li
              key={rung.step}
              className={`${styles.rung} ${done ? styles.done : ""} ${
                here ? styles.here : ""
              }`}
              aria-current={here ? "step" : undefined}
            >
              <span className={styles.pip}>{done ? "✓" : index + 1}</span>
              <span className={styles.rungName}>{rung.name}</span>
              <span className={styles.tag}>
                {done ? "Done" : here ? "Now" : ""}
              </span>
            </li>
          );
        })}
      </ol>

      {state.next === "business" ? <BusinessForm /> : null}

      {state.next === "branch" && organisationId ? (
        <BranchForm organisationId={organisationId} />
      ) : null}

      {state.next === "catalogue" && organisationId ? (
        <ProductForm
          organisationId={organisationId}
          hasTaxRule={state.taxRules > 0}
          branchId={(await branchChoices())[0]?.id ?? null}
        />
      ) : null}

      {state.next === "register" ? (
        <RegisterForm branches={await branchChoices()} />
      ) : null}

      {state.next === "done" ? (
        <section className={`${styles.card} ${styles.doneCard}`}>
          <h2 className={styles.cardTitle}>Everything a sale needs is in place</h2>
          <p className={styles.cardLede}>
            {state.branches} branch{state.branches === 1 ? "" : "es"},{" "}
            {state.products} product{state.products === 1 ? "" : "s"} and{" "}
            {state.registers} till{state.registers === 1 ? "" : "s"}.
          </p>

          <Link className={styles.link} href="/">
            Go to your dashboard
          </Link>

          <div className={styles.note}>
            <strong>One thing left before a cashier can ring anything up.</strong>{" "}
            Till staff sign in at the register with credentials that belong to
            your business and never reach Genmars — so they need adding, and
            assigning to a branch, under Staff.
          </div>
        </section>
      ) : null}
    </div>
  );
}

/**
 * The branches a register may be attached to.
 *
 * Fetched here rather than in `progress` because only this one step needs
 * them, and the list is already scoped to the caller's tenant by the server.
 */
async function branchChoices(): Promise<{ id: number; branch_name: string }[]> {
  const { getOrNull } = await import("@/lib/api");
  const page = await getOrNull<
    { results?: { id: number; branch_name: string }[] } | { id: number; branch_name: string }[]
  >("/brn/branch/");
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}
