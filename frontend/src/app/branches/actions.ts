"use server";

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

const ok: FormErrors = { field: {}, general: [] };

export async function saveBranch(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  const id = Number(text(form, "branch_id")) || null;
  if (!organisation) return { field: {}, general: ["No business."] };

  const body = {
    organization_id: organisation,
    branch_name: text(form, "branch_name"),
    branch_location: text(form, "branch_location"),
    branch_allocation: text(form, "branch_allocation") || "Main floor",
    branch_manager: text(form, "branch_manager"),
    is_active: text(form, "is_active") !== "false",
  };

  try {
    if (id) await patch(`/brn/branch/${id}/`, body);
    else await post("/brn/branch/", body);
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/branches");
  revalidatePath("/");
  return ok;
}

/**
 * Add a till.
 *
 * ⚠ `branch_id`, not `branch`. The serialiser reads a branch nested and
 *   writes it by id — see BranchesSerializer. Sending the wrong one is
 *   accepted and silently ignored, which is a register attached to nothing.
 */
export async function saveRegister(
  _previous: State,
  form: FormData,
): Promise<State> {
  const branch = Number(text(form, "branch_id"));
  const id = Number(text(form, "register_id")) || null;
  if (!branch) return { field: {}, general: ["Choose a branch."] };

  const body = {
    branch_id: branch,
    name: text(form, "name"),
    register_number: text(form, "register_number"),
    is_active: text(form, "is_active") !== "false",
  };

  try {
    if (id) await patch(`/brn/register/${id}/`, body);
    else await post("/brn/register/", body);
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/branches");
  revalidatePath("/");
  return ok;
}
