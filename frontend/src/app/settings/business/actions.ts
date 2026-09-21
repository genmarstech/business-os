"use server";

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

/**
 * Rename the business, or restate its size.
 *
 * ── THE NUMBER IS NOT RENAMEABLE, AND THAT IS WHY THIS IS SAFE ─────────────
 * `org_number` is generated and read-only. Everything that points at this
 * tenant points at its id, so a name is a label rather than an identity — two
 * shops may share one, and changing yours affects nobody else and breaks no
 * reference of your own.
 */
export async function saveBusiness(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(String(form.get("organization_id") ?? "").trim());
  if (!id) return { field: {}, general: ["No business."] };

  try {
    await patch(`/org/organizations/${id}/`, {
      name: String(form.get("name") ?? "").trim(),
      staff_size: String(form.get("staff_size") ?? "MD").trim(),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/business");
  revalidatePath("/", "layout");
  return { field: {}, general: [] };
}
