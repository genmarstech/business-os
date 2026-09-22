"use client";

import { useActionState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import {
  changeRole,
  invitePerson,
  removePerson,
  withdrawInvitation,
  type State,
} from "./actions";
import styles from "./people.module.css";

const NONE: State = null;

/**
 * The three subscriber roles, in the words of what they can actually do.
 *
 * Mirrors identity/access.py. A role is a name for a set of permissions and
 * not a permission, so the notes describe the set — somebody choosing here is
 * deciding how much of their business to hand over, and "Admin" on its own
 * does not tell them.
 */
export const ROLES = [
  {
    value: "admin",
    label: "Admin",
    note: "Runs the business day to day — branches, catalogue, stock, tax, staff assignments and every report. Cannot invite people or change the business's own details.",
  },
  {
    value: "accountant",
    label: "Accountant",
    note: "Reads the money and touches none of it. Sales, reports and receipts; no voids, no refunds, no price changes.",
  },
  {
    value: "owner",
    label: "Owner",
    note: "Everything, including inviting other people. Only give this to somebody you would trust with the bank account.",
  },
] as const;

export function InviteForm() {
  const [state, action] = useActionState(invitePerson, NONE);

  return (
    <form action={action} className={styles.form}>
      <General messages={state?.general ?? []} />

      <Row>
        <Text
          name="email"
          label="Their email"
          hint="The address on their Genmars account. It has to match exactly."
          type="email"
          placeholder="books@yourshop.co.ke"
          required
          error={state?.field.email}
        />
        <Select
          name="role"
          label="What they may do"
          defaultValue="admin"
          error={state?.field.role}
        >
          {ROLES.map((role) => (
            <option key={role.value} value={role.value}>
              {role.label}
            </option>
          ))}
        </Select>
      </Row>

      <ul className={styles.roleKey}>
        {ROLES.map((role) => (
          <li key={role.value}>
            <strong>{role.label}</strong> {role.note}
          </li>
        ))}
      </ul>

      <Submit pending="Inviting…">Invite them</Submit>
    </form>
  );
}

export function WithdrawButton({ invitationId }: { invitationId: number }) {
  const [state, action] = useActionState(withdrawInvitation, NONE);
  return (
    <form action={action}>
      <input type="hidden" name="invitation_id" value={invitationId} />
      <General messages={state?.general ?? []} />
      <button type="submit" className={styles.quiet}>
        Withdraw
      </button>
    </form>
  );
}

export function RoleForm({
  membershipId,
  role,
  who,
}: {
  membershipId: number;
  role: string;
  who: string;
}) {
  const [state, action] = useActionState(changeRole, NONE);

  return (
    <form action={action} className={styles.inline}>
      <input type="hidden" name="membership_id" value={membershipId} />
      <General messages={state?.general ?? []} />
      <select
        name="role"
        defaultValue={role}
        className={styles.roleSelect}
        aria-label={`What ${who} may do`}
      >
        {ROLES.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <button type="submit" className={styles.quiet}>
        Change
      </button>
    </form>
  );
}

export function RemoveButton({
  membershipId,
  who,
}: {
  membershipId: number;
  who: string;
}) {
  const [state, action] = useActionState(removePerson, NONE);

  return (
    <form action={action}>
      <input type="hidden" name="membership_id" value={membershipId} />
      <General messages={state?.general ?? []} />
      {/*
        The label is just "Remove". It used to interpolate the first word of
        the person's name, which turned "A Bookkeeper" into "Remove A" — a
        first name is not reliably the first word, and a button that reads
        like a typo undermines the one action on this screen somebody should
        pause over. The name goes to the screen reader instead, where it is
        useful and cannot look broken.
      */}
      <button
        type="submit"
        className={styles.danger}
        aria-label={`Remove ${who} from this business`}
      >
        Remove
      </button>
    </form>
  );
}
