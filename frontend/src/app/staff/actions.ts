"use server";

/**
 * Staff, their branch assignments, and their till logins.
 *
 * ── THREE DIFFERENT THINGS, DELIBERATELY NOT COMBINED ──────────────────────
 *
 *   a personnel record   does this person work here
 *   an assignment        at which branch, doing what
 *   a credential         may they open a till today
 *
 * They have different lifecycles — somebody is hired before they are given a
 * login and stays on the books after it is withdrawn — and the backend keeps
 * them in three tables for that reason. Collapsing them into one "add staff"
 * call here would put the seam back in the wrong place: a manager who wants to
 * stop somebody signing in would have to delete the employee.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, patch, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

/** The personnel record. No login comes with it — see the banner above. */
export async function addPerson(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  if (!organisation) {
    return { field: {}, general: ["No business to add somebody to."] };
  }

  const idNumber = text(form, "id_number");

  try {
    await post("/org/staff/", {
      organization: organisation,
      full_name: text(form, "full_name"),
      email: text(form, "email"),
      phone_number: text(form, "phone_number"),
      address: text(form, "address") || "—",
      city: text(form, "city"),
      kra_pin: text(form, "kra_pin"),
      id_number: idNumber ? Number(idNumber) : null,
      branch: Number(text(form, "branch")) || null,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/staff");
  revalidatePath("/");
  return { field: {}, general: [] };
}

export async function assignToBranch(
  _previous: State,
  form: FormData,
): Promise<State> {
  const staff = Number(text(form, "staff_member"));
  const branch = Number(text(form, "branch_id"));
  if (!staff || !branch) {
    return { field: {}, general: ["Choose a branch."] };
  }

  try {
    await post("/brn/staff-assignments/", {
      staff_member: staff,
      branch_id: branch,
      staff_assignment: text(form, "staff_assignment") || "CA",
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath(`/staff/${staff}`);
  return { field: {}, general: [] };
}

/**
 * End an assignment without deleting it.
 *
 * The row is the record that this person worked that branch in that role on
 * the days the sales say they did. Deleting it would quietly rewrite who was
 * where — blueprint §10 is about transactions, and this is the table those
 * transactions are read against.
 */
export async function endAssignment(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "assignment_id"));
  const staff = Number(text(form, "staff_member"));
  if (!id) return { field: {}, general: ["Nothing to end."] };

  try {
    await patch(`/brn/staff-assignments/${id}/`, { is_active: false });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath(`/staff/${staff}`);
  return { field: {}, general: [] };
}

/**
 * Issue a till login.
 *
 * ⚠ THE PASSWORD IS TYPED BY THE MANAGER AND IS THEREFORE KNOWN TO THEM.
 *
 * That is why the backend sets `must_change_password` and why the screen says
 * so: until the cashier changes it, nothing rung up under it is solely
 * attributable to them. This is also the reason the password is not generated
 * and shown — a manager who has to read it out has to remember it, and a
 * generated one ends up written down beside the till.
 */
export async function issueLogin(
  _previous: State,
  form: FormData,
): Promise<State> {
  const staff = Number(text(form, "staff_member"));
  if (!staff) return { field: {}, general: ["No one to issue a login to."] };

  try {
    await post("/auth/staff/credentials/", {
      staff,
      username: text(form, "username"),
      password: text(form, "password"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath(`/staff/${staff}`);
  revalidatePath("/");
  return { field: {}, general: [] };
}

export async function resetLoginPassword(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "credential_id"));
  const staff = Number(text(form, "staff_member"));
  if (!id) return { field: {}, general: ["No login to reset."] };

  try {
    await post(`/auth/staff/credentials/${id}/reset-password/`, {
      password: text(form, "password"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath(`/staff/${staff}`);
  return { field: {}, general: ["Password changed. Any open till was signed out."] };
}

export async function setLoginActive(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "credential_id"));
  const staff = Number(text(form, "staff_member"));
  const active = text(form, "is_active") === "true";
  if (!id) return { field: {}, general: ["No login to change."] };

  try {
    await post(`/auth/staff/credentials/${id}/set-active/`, {
      is_active: active,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath(`/staff/${staff}`);
  revalidatePath("/");
  return {
    field: {},
    general: [
      active
        ? "They can sign in again."
        : "Withdrawn. Any till they had open was signed out.",
    ],
  };
}
