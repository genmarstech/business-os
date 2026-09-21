import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { PERM, may, me as whoAmI } from "@/lib/session";
import {
  AssignForm,
  EndAssignmentButton,
  IssueLoginForm,
  ResetPasswordForm,
  SetActiveForm,
} from "../forms";
import { staffBoard } from "../data";
import styles from "../staff.module.css";

/**
 * One person: where they work, and whether they can open a till.
 *
 * ── A PERSON WHO IS NOT YOURS IS NOT FOUND ─────────────────────────────────
 * The lists behind this are scoped by the server, so somebody else's employee
 * simply is not in them and this renders a 404. That is the intended answer
 * rather than a 403 — identity/scoping.py is emphatic that confirming a row
 * exists is the same enumeration oracle in a different costume, and a URL
 * with an id in it is the easiest place in the application to probe.
 */

export const dynamic = "force-dynamic";

export default async function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const me = await whoAmI();
  if (!me) redirect("/");

  const { id } = await params;
  const staffId = Number(id);
  if (!Number.isFinite(staffId)) notFound();

  const board = await staffBoard();
  const person = board.people.find((p) => p.id === staffId);
  if (!person) notFound();

  const assignments = board.assignments.filter(
    (a) => a.staff_member === staffId,
  );
  const live = assignments.filter((a) => a.is_active);
  const ended = assignments.filter((a) => !a.is_active);
  const login = board.credentials.find((c) => c.staff === staffId);
  const allowed = may(me, PERM.staffManage);

  // First initial and surname, the way a shop would write it anyway. Only a
  // suggestion — the field is editable and the manager may ignore it.
  const parts = person.full_name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0] ?? "";
  const last = parts[parts.length - 1] ?? "";
  const suggestion = (parts.length > 1 ? first.slice(0, 1) + last : first)
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>
            <Link className={styles.back} href="/staff">
              Staff
            </Link>
          </p>
          <h1 className={styles.title}>{person.full_name}</h1>
          <p className={styles.sub}>
            {person.email}
            {person.phone_number ? ` · ${person.phone_number}` : ""}
            {person.staff_number ? ` · ${person.staff_number}` : ""}
          </p>
        </header>

        {!allowed ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Only an owner manages staff.
            </p>
          </section>
        ) : (
          <>
            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Where they work</h2>
              <p className={styles.panelLede}>
                An assignment is a branch and a job, together. It is what
                decides what they may do — and it decides it{" "}
                <strong>per branch</strong>: somebody can be a cashier at one
                shop and the assistant manager at another without becoming a
                manager at both.
              </p>

              {live.length === 0 ? (
                <p className={styles.empty}>
                  Nothing yet. Until they are assigned somewhere they can sign
                  in and do nothing.
                </p>
              ) : (
                <ul className={styles.cards}>
                  {live.map((a) => (
                    <li key={a.id} className={styles.card}>
                      <div>
                        <div className={styles.name}>
                          {a.staff_assignment_display}
                        </div>
                        <div className={styles.meta}>
                          {a.branch?.branch_name ?? "a branch"}
                        </div>
                      </div>
                      <EndAssignmentButton
                        assignmentId={a.id}
                        staffId={staffId}
                        branchName={a.branch?.branch_name ?? "that branch"}
                        roleName={a.staff_assignment_display.toLowerCase()}
                      />
                    </li>
                  ))}
                </ul>
              )}

              {board.branches.length > 0 ? (
                <AssignForm staffId={staffId} branches={board.branches} />
              ) : (
                <p className={styles.empty}>Add a branch first.</p>
              )}

              {ended.length > 0 ? (
                <div className={styles.note}>
                  <strong>
                    {ended.length} past assignment
                    {ended.length === 1 ? "" : "s"}, kept.
                  </strong>{" "}
                  An ended assignment is the record that they worked that
                  branch in that role on the days the sales say they did.
                  Deleting it would quietly rewrite who was where.
                </div>
              ) : null}
            </section>

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Their till sign-in</h2>

              {!login ? (
                <>
                  <p className={styles.panelLede}>
                    A username and password that belong to this business. They
                    never reach Genmars, so nobody here can recover them for
                    you — you reset them yourself, below, once this exists.
                  </p>
                  <IssueLoginForm staffId={staffId} suggestion={suggestion} />
                </>
              ) : (
                <>
                  <p className={styles.panelLede}>
                    Signs in as <span className={styles.mono}>{login.username}</span>
                    {login.is_active ? "" : " — currently withdrawn"}
                    {login.is_locked
                      ? " — locked after too many wrong passwords"
                      : ""}
                    .
                  </p>

                  {login.must_change_password ? (
                    <div className={styles.note}>
                      <strong>They are still on the password you typed.</strong>{" "}
                      Until they choose their own, anything rung up under this
                      sign-in is something you could also have done — which is
                      exactly what makes a till count disputable.
                    </div>
                  ) : null}

                  <ResetPasswordForm
                    credentialId={login.id}
                    staffId={staffId}
                  />

                  <div className={styles.divide} />

                  <p className={styles.panelLede}>
                    {login.is_active
                      ? "Withdrawing signs them out of any till they have open, immediately — not at the end of the shift. Their record and their sales history stay."
                      : "They cannot sign in. Everything they have ever rung up is still on the books."}
                  </p>
                  <SetActiveForm
                    credentialId={login.id}
                    staffId={staffId}
                    active={!login.is_active}
                  />
                </>
              )}
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
