import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { BranchForm, RegisterForm } from "./forms";
import styles from "./branches.module.css";

/**
 * Where the shop trades, and what it trades on.
 *
 * ── A BRANCH IS NEVER DELETED FROM HERE ────────────────────────────────────
 * Sales, stock movements and staff assignments all point at one, and a branch
 * that could vanish would take the meaning of every figure recorded against
 * it with it. Closing one is `is_active: false` — it stops appearing at a
 * till and stays in every report it was ever in.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Branches" };

type Branch = {
  id: number;
  branch_name: string;
  branch_location: string;
  branch_allocation: string;
  branch_manager: string;
  branch_number?: string;
  is_active: boolean;
};

type Register = {
  id: number;
  name: string;
  register_number: string;
  is_active: boolean;
  branch: { id: number; branch_name: string } | null;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function BranchesPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [branchPage, registerPage] = await Promise.all([
    getOrNull<Page<Branch>>("/brn/branch/"),
    getOrNull<Page<Register>>("/brn/register/"),
  ]);

  const branches = rows(branchPage);
  const registers = rows(registerPage);
  const organisationId =
    me.kind === "subscriber" ? me.organisations[0]?.id : me.organisation.id;
  const canManage = may(me, PERM.branchManage);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Branches</h1>
          <p className={styles.sub}>
            A branch is a physical location; a till is one point of sale inside
            it. Stock, staff and takings all belong to a branch, which is what
            lets you compare them.
          </p>
        </header>

        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>
            {branches.length === 0
              ? "No branches yet"
              : `${branches.length} branch${branches.length === 1 ? "" : "es"}`}
          </h2>

          {branches.length === 0 ? (
            <p className={styles.empty}>
              Nothing works without one — stock, staff and sales all belong to
              a branch.
            </p>
          ) : (
            <ul className={styles.cards}>
              {branches.map((branch) => {
                const tills = registers.filter(
                  (r) => r.branch?.id === branch.id,
                );
                return (
                  <li
                    key={branch.id}
                    className={`${styles.card} ${
                      branch.is_active ? "" : styles.closed
                    }`}
                  >
                    <div className={styles.cardHead}>
                      <div>
                        <div className={styles.name}>{branch.branch_name}</div>
                        <div className={styles.meta}>
                          {branch.branch_location}
                          {branch.branch_allocation
                            ? ` · ${branch.branch_allocation}`
                            : ""}
                        </div>
                      </div>
                      {branch.is_active ? null : (
                        <span className={styles.badge}>Closed</span>
                      )}
                    </div>

                    <div className={styles.meta}>
                      Run by {branch.branch_manager}
                      {branch.branch_number ? ` · ${branch.branch_number}` : ""}
                    </div>

                    <div className={styles.tills}>
                      {tills.length === 0 ? (
                        <span className={styles.warn}>
                          No till — nothing can be sold here
                        </span>
                      ) : (
                        tills.map((till) => (
                          <span key={till.id} className={styles.till}>
                            {till.name}
                            <span className={styles.tillNumber}>
                              {till.register_number}
                            </span>
                          </span>
                        ))
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {canManage && organisationId ? (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Add a branch</h2>
              <BranchForm organisationId={organisationId} />
            </section>

            {branches.length > 0 ? (
              <section className={styles.panel}>
                <h2 className={styles.panelTitle}>Add a till</h2>
                <p className={styles.panelLede}>
                  Cash and sales are counted per till, which is how a drawer
                  gets reconciled at the end of a shift. A busy counter with
                  two people wants two.
                </p>
                <RegisterForm
                  branches={branches.filter((b) => b.is_active)}
                />
              </section>
            ) : null}
          </>
        ) : null}
      </div>
    </Shell>
  );
}
