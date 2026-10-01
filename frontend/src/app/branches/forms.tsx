"use client";

import { useActionState, useState } from "react";

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

export type Branch = {
  id: number;
  branch_name: string;
  branch_location: string;
  branch_allocation: string;
  branch_manager: string;
  is_active: boolean;
};

/**
 * Adding a branch, or changing one.
 *
 * One form for both, because they are the same fields and a second copy is
 * a second place for one of them to be forgotten. `branch` absent means a
 * new one; present means an edit, and the hidden `branch_id` is what makes
 * `saveBranch` PATCH instead of POST.
 */
export function BranchForm({
  organisationId,
  branch,
  onDone,
}: {
  organisationId: number;
  branch?: Branch;
  onDone?: () => void;
}) {
  const [state, action] = useActionState(saveBranch, NONE);
  const editing = branch !== undefined;

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisationId} />
      {editing ? (
        <input type="hidden" name="branch_id" value={branch.id} />
      ) : null}
      {/*
        Carried through the edit form so renaming a branch does not quietly
        reopen a closed one — `saveBranch` reads anything that is not the
        string "false" as open.
      */}
      {editing ? (
        <input
          type="hidden"
          name="is_active"
          value={String(branch.is_active)}
        />
      ) : null}
      <General messages={state?.general ?? []} />
      {saved(state) ? (
        <p className={styles.saved}>{editing ? "Saved." : "Added."}</p>
      ) : null}

      <Row>
        <Text
          name="branch_name"
          label="Branch name"
          placeholder="Karen"
          required
          defaultValue={branch?.branch_name}
          error={state?.field.branch_name}
        />
        <Text
          name="branch_location"
          label="Town or area"
          placeholder="Nairobi"
          required
          defaultValue={branch?.branch_location}
          error={state?.field.branch_location}
        />
      </Row>

      <Row>
        <Text
          name="branch_allocation"
          label="Where in the building"
          hint="Optional. Ground floor, Shop 4."
          placeholder="Ground floor"
          defaultValue={branch?.branch_allocation}
          error={state?.field.branch_allocation}
        />
        <Text
          name="branch_manager"
          label="Who runs it"
          placeholder="A Manager"
          required
          defaultValue={branch?.branch_manager}
          error={state?.field.branch_manager}
        />
      </Row>

      <div className={styles.formActions}>
        <Submit pending={editing ? "Saving…" : "Adding…"}>
          {editing ? "Save changes" : "Add branch"}
        </Submit>
        {onDone ? (
          <button type="button" className={styles.quietButton} onClick={onDone}>
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );
}

/**
 * Rename or correct one branch, in place.
 *
 * Shown only on request: four always-open forms down a page of branches is
 * a page nobody can scan, and renaming a branch is a rare act.
 */
export function EditBranch({
  organisationId,
  branch,
}: {
  organisationId: number;
  branch: Branch;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quietButton} onClick={() => setOpen(true)}>
        Edit
      </button>
    );
  }
  return (
    <BranchForm
      organisationId={organisationId}
      branch={branch}
      onDone={() => setOpen(false)}
    />
  );
}

/**
 * Closing a branch, and reopening it.
 *
 * ── THERE IS NO DELETE, AND THERE SHOULD NOT BE ─────────────────────────
 * Sales, stock movements, staff assignments and purchase orders all point
 * at a branch, and most of those are PROTECT. Deleting one would either be
 * refused by the database or take the shop's own history with it. Closing
 * is what a business actually does to a branch, and it is reversible.
 */
export function CloseBranch({
  organisationId,
  branch,
}: {
  organisationId: number;
  branch: Branch;
}) {
  const [state, action] = useActionState(saveBranch, NONE);

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="organization_id" value={organisationId} />
      <input type="hidden" name="branch_id" value={branch.id} />
      <input type="hidden" name="branch_name" value={branch.branch_name} />
      <input
        type="hidden"
        name="branch_location"
        value={branch.branch_location}
      />
      <input
        type="hidden"
        name="branch_allocation"
        value={branch.branch_allocation}
      />
      <input
        type="hidden"
        name="branch_manager"
        value={branch.branch_manager}
      />
      <input
        type="hidden"
        name="is_active"
        value={String(!branch.is_active)}
      />
      <General messages={state?.general ?? []} />
      <Submit pending="…">{branch.is_active ? "Close" : "Reopen"}</Submit>
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
