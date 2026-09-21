"use client";

import { useActionState } from "react";

import { General, Select, Submit, Text } from "@/components/Form";
import { saveBusiness, type State } from "./actions";
import styles from "../../branches/branches.module.css";

const NONE: State = null;

export function BusinessForm({
  organisation,
}: {
  organisation: { id: number; name: string; staff_size?: string };
}) {
  const [state, action] = useActionState(saveBusiness, NONE);
  const saved =
    state !== null &&
    state.general.length === 0 &&
    Object.keys(state.field).length === 0;

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisation.id} />
      <General messages={state?.general ?? []} />
      {saved ? (
        <p className={styles.saved}>
          Saved. Receipts printed from now on carry the new name.
        </p>
      ) : null}

      <Text
        name="name"
        label="Business name"
        hint="What customers see on a receipt and what you see at the top of every screen."
        required
        defaultValue={organisation.name}
        error={state?.field.name}
      />

      <Select
        name="staff_size"
        label="Roughly how many people work here"
        hint="Only used to size the plan. It changes nothing you can do."
        defaultValue={organisation.staff_size ?? "MD"}
        error={state?.field.staff_size}
      >
        <option value="SM">Around 5</option>
        <option value="MD">Around 15</option>
        <option value="LG">25 or more</option>
      </Select>

      <Submit pending="Saving…">Save</Submit>
    </form>
  );
}
