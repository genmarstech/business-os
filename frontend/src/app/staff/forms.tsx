"use client";

import { useActionState, useState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import {
  addPerson,
  assignToBranch,
  endAssignment,
  issueLogin,
  resetLoginPassword,
  sendLoginDetails,
  setLoginActive,
  type State,
} from "./actions";
import { ROLES, type Branch } from "./shape";
import styles from "./staff.module.css";

const NONE: State = null;

export function AddPersonForm({
  organisationId,
  branches,
}: {
  organisationId: number;
  branches: Branch[];
}) {
  const [state, action] = useActionState(addPerson, NONE);
  const saved = state !== null && !state.general.length && !Object.keys(state.field).length;

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="organization_id" value={organisationId} />
      <General messages={state?.general ?? []} />
      {saved ? <p className={styles.saved}>Added.</p> : null}

      <Row>
        <Text
          name="full_name"
          label="Full name"
          placeholder="Jane Mwangi"
          required
          error={state?.field.full_name}
        />
        <Text
          name="phone_number"
          label="Phone"
          placeholder="+254700000000"
          required
          mono
          error={state?.field.phone_number}
        />
      </Row>

      <Row>
        <Text
          name="email"
          label="Email"
          type="email"
          placeholder="jane@example.com"
          required
          error={state?.field.email}
        />
        <Text
          name="id_number"
          label="ID number"
          hint="The national ID. It is how you tell two Jane Mwangis apart."
          inputMode="numeric"
          mono
          required
          error={state?.field.id_number}
        />
      </Row>

      <Row>
        <Text
          name="city"
          label="Town"
          placeholder="Nairobi"
          error={state?.field.city}
        />
        <Text
          name="kra_pin"
          label="KRA PIN"
          hint="Optional. Needed only when you run payroll through this."
          mono
          error={state?.field.kra_pin}
        />
      </Row>

      <Select
        name="branch"
        label="Based at"
        hint="Where they normally work. What they may DO there is the assignment on their own page."
        error={state?.field.branch}
      >
        {branches.map((branch) => (
          <option key={branch.id} value={branch.id}>
            {branch.branch_name}
          </option>
        ))}
      </Select>

      <Submit pending="Adding…">Add to the team</Submit>
    </form>
  );
}

export function AssignForm({
  staffId,
  branches,
}: {
  staffId: number;
  branches: Branch[];
}) {
  const [state, action] = useActionState(assignToBranch, NONE);

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />

      <Row>
        <Select
          name="branch_id"
          label="Branch"
          required
          error={state?.field.branch_id}
        >
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>

        <Select
          name="staff_assignment"
          label="Doing what"
          defaultValue="CA"
          error={state?.field.staff_assignment}
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

      <Submit pending="Assigning…">Assign</Submit>
    </form>
  );
}

export function EndAssignmentButton({
  assignmentId,
  staffId,
  branchName,
  roleName,
}: {
  assignmentId: number;
  staffId: number;
  branchName: string;
  roleName: string;
}) {
  const [state, action] = useActionState(endAssignment, NONE);

  return (
    <form action={action}>
      <input type="hidden" name="assignment_id" value={assignmentId} />
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />
      <button type="submit" className={styles.quiet}>
        End {roleName} at {branchName}
      </button>
    </form>
  );
}

/**
 * Create a till login — and by default, do not create a password for it.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE PASSWORD FIELD IS OPTIONAL, AND IT IS BEHIND A DISCLOSURE FOR A REASON.
 *
 * It was required, so every manager's first act was to invent a credential
 * two people knew — and the habit that produced is the one worth stopping:
 * type a password, read it out or send it over WhatsApp, where it stays for
 * ever. Leaving it empty creates a login nobody can use until the employee
 * sets it up from a code sent to their own address.
 *
 * A field that is merely marked optional gets filled in anyway, because a
 * password box on a login form reads as the thing you are here to do. So the
 * default path has no password box at all, and typing one is a deliberate
 * detour with the reason to avoid it written beside it.
 *
 * The detour stays because it has a real case: an employee with no email
 * address on file still has to be able to get on a till.
 * ══════════════════════════════════════════════════════════════════════════
 */
export function IssueLoginForm({
  staffId,
  suggestion,
  email,
}: {
  staffId: number;
  suggestion: string;
  email: string;
}) {
  const [state, action] = useActionState(issueLogin, NONE);
  // Defaults open when there is nowhere to send a code, because then it is
  // not a detour — it is the only way through, and a manager should not have
  // to find that out by pressing the button and reading an error.
  const [typeOne, setTypeOne] = useState(!email);

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />

      <Text
        name="username"
        label="Username"
        hint="Theirs alone, inside your business. Another shop may use the same one."
        defaultValue={suggestion}
        mono
        required
        error={state?.field.username}
      />

      {email && !typeOne ? (
        <>
          <p className={styles.hint}>
            {email} gets their username, your business number and a code to
            choose a password with. No password is emailed and you never see
            one — which is what makes everything they ring up solely theirs.
          </p>
          <button
            type="button"
            className={styles.quiet}
            onClick={() => setTypeOne(true)}
          >
            I would rather type a password myself
          </button>
        </>
      ) : null}

      {typeOne ? (
        <>
          <Text
            name="password"
            label="First password"
            hint="At least 8 characters. They will be asked to change it."
            type="password"
            autoComplete="new-password"
            required
            error={state?.field.password}
          />
          {email ? (
            <p className={styles.hint}>
              You will know this password, so until they change it nothing
              rung up under it is solely theirs — which is what makes a till
              count disputable.
            </p>
          ) : (
            /*
              Said before the button is pressed rather than as an error
              after. The fix is on their staff record, which is the screen
              the manager is already on.
            */
            <p className={styles.hint}>
              They have no email address on file, so a setup code has nowhere
              to go. Add one above and you can let them pick their own
              instead.
            </p>
          )}
          {email ? (
            <button
              type="button"
              className={styles.quiet}
              onClick={() => setTypeOne(false)}
            >
              Email them a setup code instead
            </button>
          ) : null}
        </>
      ) : null}

      <Submit pending="Creating…">
        {typeOne ? "Create their sign-in" : "Create it and email their code"}
      </Submit>
    </form>
  );
}

/**
 * Send their login details again.
 *
 * ── THE FIRST THING A MANAGER SHOULD REACH FOR ──────────────────────────
 * Above "set a new password", because the alternative a manager reaches
 * for otherwise is to type a password and send it over WhatsApp — at
 * which point two people know it and nothing the cashier rings up is
 * solely theirs. This sends a single-use code and the cashier chooses
 * their own.
 *
 * Creating the login already sends one, so this is for the two cases that
 * come after: an email that bounced, and somebody who never got round to
 * using the first code. `needs_setup` decides which letter goes out — the
 * server reads it off the credential, so this screen only has to say which
 * to expect.
 */
export function SendLoginForm({
  credentialId,
  staffId,
  email,
  needsSetup,
}: {
  credentialId: number;
  staffId: number;
  email: string;
  needsSetup: boolean;
}) {
  const [state, action] = useActionState(sendLoginDetails, NONE);

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="credential_id" value={credentialId} />
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />

      {email ? (
        <p className={styles.hint}>
          {needsSetup
            ? `Sends ${email} their username, your business number and a setup code — the same first-day email, again. Any earlier code stops working.`
            : `Sends ${email} a code to choose a new password with. No password is ever emailed — they pick one nobody else knows.`}
        </p>
      ) : (
        /*
          Said before the button is pressed rather than as an error after.
          The fix is on their staff record, which is the screen they are
          already on.
        */
        <p className={styles.hint}>
          They have no email address on file, so there is nowhere to send it.
          Add one above first.
        </p>
      )}

      <Submit pending="Sending…">
        {needsSetup ? "Send their setup code again" : "Email their login details"}
      </Submit>
    </form>
  );
}

/**
 * Set a password on somebody's login yourself.
 *
 * ── ALSO THE WAY OUT WHEN A SETUP CODE IS NOT GETTING THROUGH ───────────
 * It works on a login still waiting to be set up exactly as it does on a
 * live one, which is what stops the emailed-code default from being able to
 * keep somebody off a till: mail down, address wrong, employee standing
 * there at seven in the morning — this gets them working now. The cost is
 * the one it always was, and it is said on the screen.
 */
export function ResetPasswordForm({
  credentialId,
  staffId,
  needsSetup,
}: {
  credentialId: number;
  staffId: number;
  needsSetup: boolean;
}) {
  const [state, action] = useActionState(resetLoginPassword, NONE);

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="credential_id" value={credentialId} />
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />

      <Text
        name="password"
        label="New password"
        hint={
          needsSetup
            ? "If their setup code is not getting through and they need to work now, this puts them on a till immediately — but you will know the password, so ask them to change it."
            : "Use this when somebody has forgotten theirs, or been locked out."
        }
        type="password"
        autoComplete="new-password"
        required
        error={state?.field.password}
      />

      <Submit pending="Changing…">Set a new password</Submit>
    </form>
  );
}

export function SetActiveForm({
  credentialId,
  staffId,
  active,
}: {
  credentialId: number;
  staffId: number;
  active: boolean;
}) {
  const [state, action] = useActionState(setLoginActive, NONE);

  return (
    <form action={action}>
      <input type="hidden" name="credential_id" value={credentialId} />
      <input type="hidden" name="staff_member" value={staffId} />
      <input type="hidden" name="is_active" value={active ? "true" : "false"} />
      <General messages={state?.general ?? []} />
      <Submit pending="Working…">
        {active ? "Let them sign in again" : "Withdraw their sign-in"}
      </Submit>
    </form>
  );
}
