"use client";

import { useActionState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import {
  addPerson,
  assignToBranch,
  endAssignment,
  issueLogin,
  resetLoginPassword,
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

export function IssueLoginForm({
  staffId,
  suggestion,
}: {
  staffId: number;
  suggestion: string;
}) {
  const [state, action] = useActionState(issueLogin, NONE);

  return (
    <form action={action} className={styles.panelForm}>
      <input type="hidden" name="staff_member" value={staffId} />
      <General messages={state?.general ?? []} />

      <Row>
        <Text
          name="username"
          label="Username"
          hint="Theirs alone, inside your business. Another shop may use the same one."
          defaultValue={suggestion}
          mono
          required
          error={state?.field.username}
        />
        <Text
          name="password"
          label="First password"
          hint="At least 8 characters. They will be asked to change it."
          type="password"
          autoComplete="new-password"
          required
          error={state?.field.password}
        />
      </Row>

      <Submit pending="Creating…">Create their sign-in</Submit>
    </form>
  );
}

export function ResetPasswordForm({
  credentialId,
  staffId,
}: {
  credentialId: number;
  staffId: number;
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
        hint="Use this when somebody has forgotten theirs, or been locked out."
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
