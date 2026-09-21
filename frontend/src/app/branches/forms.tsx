"use client";

import { useActionState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import { saveBranch, saveRegister, type State } from "./actions";
import styles from "./branches.module.css";

const NONE: State = null;

function saved(state: State): boolean {
  return (
    state !== null &&
    state.general.length === 0 &&
    Object.keys(state.field).length === 0
  );
}

export function BranchForm({ organisationId }: { organisationId: number }) {
  const [state, action] = useActionState(saveBranch, NONE);

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisationId} />
      <General messages={state?.general ?? []} />
      {saved(state) ? <p className={styles.saved}>Added.</p> : null}

      <Row>
        <Text
          name="branch_name"
          label="Branch name"
          placeholder="Karen"
          required
          error={state?.field.branch_name}
        />
        <Text
          name="branch_location"
          label="Town or area"
          placeholder="Nairobi"
          required
          error={state?.field.branch_location}
        />
      </Row>

      <Row>
        <Text
          name="branch_allocation"
          label="Where in the building"
          hint="Optional. Ground floor, Shop 4."
          placeholder="Ground floor"
          error={state?.field.branch_allocation}
        />
        <Text
          name="branch_manager"
          label="Who runs it"
          placeholder="A Manager"
          required
          error={state?.field.branch_manager}
        />
      </Row>

      <Submit pending="Adding…">Add branch</Submit>
    </form>
  );
}

export function RegisterForm({
  branches,
}: {
  branches: { id: number; branch_name: string }[];
}) {
  const [state, action] = useActionState(saveRegister, NONE);

  return (
    <form action={action} className={styles.form}>
      <General messages={state?.general ?? []} />
      {saved(state) ? <p className={styles.saved}>Added.</p> : null}

      <Row>
        <Select
          name="branch_id"
          label="At which branch"
          required
          error={state?.field.branch_id}
        >
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>
        <Text
          name="name"
          label="What to call it"
          placeholder="Till 2"
          required
          error={state?.field.name}
        />
      </Row>

      <Text
        name="register_number"
        label="Number"
        hint="Yours to choose. Unique within the branch only — another branch may use it too."
        placeholder="T2"
        mono
        required
        error={state?.field.register_number}
      />

      <Submit pending="Adding…">Add till</Submit>
    </form>
  );
}
