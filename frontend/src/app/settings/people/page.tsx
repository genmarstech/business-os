import Link from "next/link";
import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { PERM, may, me as whoAmI } from "@/lib/session";
import { InviteForm, RemoveButton, RoleForm, WithdrawButton } from "./forms";
import styles from "./people.module.css";

/**
 * Who administers this business.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * NOT THE SAME PEOPLE AS /staff, AND THE SCREEN HAS TO SAY SO.
 *
 * CLAUDE.md splits principals by who the commercial relationship is with. The
 * people here deal with GENMARS and sign in with a Genmars account. The people
 * under Staff work for the SHOP, hold credentials that never reach us, and are
 * hired and let go without asking anybody.
 *
 * Two lists, two doors, two credential stores that never cross. Merging them
 * on screen is how somebody ends up believing a cashier can be promoted into
 * an administrator, or that sacking one revokes the other.
 * ══════════════════════════════════════════════════════════════════════════
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "People" };

type Member = {
  id: number;
  email: string;
  full_name: string;
  role: string;
  role_label: string;
  invited_by_email?: string;
};

type Invitation = {
  id: number;
  email: string;
  role: string;
  role_label: string;
  state: "waiting" | "accepted" | "withdrawn" | "expired" | string;
  expires_at: string;
  invited_by_email?: string;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export default async function PeoplePage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const [memberPage, invitePage] = await Promise.all([
    getOrNull<Page<Member>>("/auth/members/"),
    getOrNull<Page<Invitation>>("/auth/invitations/"),
  ]);

  const members = rows(memberPage);
  const invitations = rows(invitePage).filter((i) => i.state === "waiting");
  const canManage = may(me, PERM.membersManage);
  const myEmail = me.kind === "subscriber" ? me.email : "";
  const owners = members.filter((m) => m.role === "owner").length;

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Organisation</p>
          <h1 className={styles.title}>People</h1>
          <p className={styles.sub}>
            The people who administer this business. They sign in with their
            own Genmars account — there is no password here for you to set or
            reset. Cashiers and branch staff are a different list, under{" "}
            <Link className={styles.quietLink} href="/staff">
              Staff
            </Link>
            , with credentials that belong to you and never reach Genmars.
          </p>
        </header>

        <section className={styles.panel}>
          <h2 className={styles.panelTitle}>
            {members.length === 1
              ? "Just you, so far"
              : `${members.length} people`}
          </h2>

          <div className={styles.scroll}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Who</th>
                  <th>What they may do</th>
                  {canManage ? <th /> : null}
                </tr>
              </thead>
              <tbody>
                {members.map((member) => {
                  const isMe = member.email === myEmail;
                  const lastOwner = member.role === "owner" && owners === 1;
                  return (
                    <tr key={member.id}>
                      <td>
                        <div className={styles.name}>
                          {member.full_name || member.email}
                          {isMe ? <span className={styles.you}>you</span> : null}
                        </div>
                        <div className={styles.meta}>{member.email}</div>
                        {member.invited_by_email ? (
                          <div className={styles.meta}>
                            invited by {member.invited_by_email}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        {canManage && !lastOwner ? (
                          <RoleForm
                            membershipId={member.id}
                            role={member.role}
                            who={member.full_name || member.email}
                          />
                        ) : (
                          <>
                            <div>{member.role_label}</div>
                            {lastOwner ? (
                              <div className={styles.meta}>
                                The only owner — cannot be changed
                              </div>
                            ) : null}
                          </>
                        )}
                      </td>
                      {canManage ? (
                        <td className={styles.right}>
                          {lastOwner ? null : (
                            <RemoveButton
                              membershipId={member.id}
                              who={
                                isMe
                                  ? "yourself"
                                  : member.full_name || member.email
                              }
                            />
                          )}
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {owners === 1 ? (
            <div className={styles.note}>
              <strong>There is one owner, and that cannot go to zero.</strong> A
              business with nobody who can administer it cannot be recovered —
              the authority to recover it is the thing that would have been
              removed. If you want to step back, make somebody else an owner
              first.
            </div>
          ) : null}
        </section>

        {!canManage ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              Only an owner invites people or changes what they may do.
            </p>
          </section>
        ) : (
          <>
            {invitations.length > 0 ? (
              <section className={styles.panel}>
                <h2 className={styles.panelTitle}>Waiting to be accepted</h2>
                <p className={styles.panelLede}>
                  Nothing has been granted yet. Each of these becomes real the
                  moment that person signs in with a Genmars account on that
                  exact address — there is no link for them to click, and
                  nothing to forward.
                </p>

                <ul className={styles.cards}>
                  {invitations.map((invitation) => (
                    <li key={invitation.id} className={styles.card}>
                      <div>
                        <div className={styles.name}>{invitation.email}</div>
                        <div className={styles.meta}>
                          as {invitation.role_label} · expires{" "}
                          {new Date(invitation.expires_at).toLocaleDateString(
                            "en-KE",
                          )}
                        </div>
                      </div>
                      <WithdrawButton invitationId={invitation.id} />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Invite somebody</h2>
              <p className={styles.panelLede}>
                They need a Genmars account on the address you type. If they do
                not have one, they will be asked to make one on the way in —
                and it has to be that same address, because the invitation is
                matched against it rather than against a link.
              </p>
              <InviteForm />
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
