import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { AddPersonForm } from "./forms";
import { staffBoard, type Assignment, type Credential } from "./data";
import styles from "./staff.module.css";

/**
 * Everybody who works here, and whether they can get in.
 *
 * ── TWO COLUMNS ANSWER TWO DIFFERENT QUESTIONS ─────────────────────────────
 * "Where do they work" and "can they sign in" are separate facts about a
 * person and are shown separately, because the common mistake this screen has
 * to prevent is assuming one implies the other. Somebody assigned as a cashier
 * with no login cannot open a till; somebody with a login and no assignment
 * can sign in and do nothing at all. Both are visible at a glance rather than
 * discovered at the counter.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Staff" };

export default async function StaffPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const board = await staffBoard();
  const organisationId =
    me.kind === "subscriber" ? me.organisations[0]?.id : me.organisation.id;

  /*
   * Not security — the API refuses either way. This is so somebody without
   * the permission reads one sentence instead of an empty table they will
   * assume is a bug.
   */
  const allowed = may(me, PERM.staffManage);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>Staff</h1>
          <p className={styles.sub}>
            Your employees, not Genmars accounts. They sign in at the till with
            a username that belongs to this business, and their password never
            reaches us — so adding and removing them is yours to do, and takes
            effect at once.
          </p>
        </header>

        {!allowed ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Only an owner manages staff. Ask whoever owns the business to add
              somebody or issue a till sign-in.
            </p>
          </section>
        ) : (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>
                {board.people.length === 0
                  ? "Nobody works here yet"
                  : `${board.people.length} on the books`}
              </h2>

              {board.people.length === 0 ? (
                <p className={styles.empty}>
                  A till cannot take a sale until somebody can open it. Add the
                  person first; their branch and their sign-in come next, on
                  their own page.
                </p>
              ) : (
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Where, and what</th>
                      <th>Till sign-in</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {board.people.map((person) => {
                      const theirs = board.assignments.filter(
                        (a) => a.staff_member === person.id && a.is_active,
                      );
                      const login = board.credentials.find(
                        (c) => c.staff === person.id,
                      );
                      return (
                        <tr key={person.id}>
                          <td>
                            <div className={styles.name}>{person.full_name}</div>
                            <div className={styles.meta}>{person.email}</div>
                          </td>
                          <td>
                            <Where assignments={theirs} />
                          </td>
                          <td>
                            <Login login={login} />
                          </td>
                          <td className={styles.right}>
                            <Link
                              className={styles.quiet}
                              href={`/staff/${person.id}`}
                            >
                              Manage
                            </Link>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </section>

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Add somebody</h2>
              <p className={styles.panelLede}>
                The personnel record only. No sign-in comes with it — people
                are hired before they are trusted with a till, and stay on the
                books after they stop working one.
              </p>

              {board.branches.length === 0 ? (
                <p className={styles.empty}>
                  Add a branch first. Everybody works somewhere.
                </p>
              ) : organisationId ? (
                <AddPersonForm
                  organisationId={organisationId}
                  branches={board.branches}
                />
              ) : null}
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}

function Where({ assignments }: { assignments: Assignment[] }) {
  if (assignments.length === 0) {
    return (
      <span className={styles.warn}>
        Not assigned — they can do nothing yet
      </span>
    );
  }
  return (
    <ul className={styles.tight}>
      {assignments.map((a) => (
        <li key={a.id}>
          {a.staff_assignment_display} at {a.branch?.branch_name ?? "a branch"}
        </li>
      ))}
    </ul>
  );
}

/**
 * The state of somebody's login, in the words a manager would use.
 *
 * "Locked" and "withdrawn" are different things and look identical from behind
 * a counter — one waits out, one needs a person. Saying which is the whole
 * value of this cell.
 */
function Login({ login }: { login?: Credential }) {
  if (!login) return <span className={styles.warn}>None — cannot sign in</span>;
  if (!login.is_active)
    return <span className={styles.warn}>Withdrawn</span>;
  if (login.is_locked)
    return (
      <span className={styles.warn}>
        Locked after too many tries — reset to clear it
      </span>
    );
  return (
    <>
      <span className={styles.mono}>{login.username}</span>
      {login.must_change_password ? (
        <div className={styles.meta}>
          Still using the password you set for them
        </div>
      ) : null}
    </>
  );
}
