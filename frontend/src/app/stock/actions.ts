"use server";

/**
 * Moving stock, outside a sale.
 *
 * ⚠ THE QUANTITY IS NEVER ASSIGNED, ONLY MOVED BY AN AMOUNT.
 *
 * "Set the shelf to 34" and "24 arrived" are different facts, and only the
 * second explains anything. The endpoint takes a delta and writes the
 * movement that goes with it — see inventory/services.py. A screen that let
 * somebody type a new total would be back to a quantity nobody can account
 * for.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

export async function adjustStock(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "inventory_id"));
  const direction = text(form, "direction") === "out" ? -1 : 1;
  const size = text(form, "quantity");

  if (!id) return { field: {}, general: ["No stock line."] };
  if (!size || Number(size) <= 0) {
    return { field: { quantity: "How many?" }, general: [] };
  }

  try {
    await post(`/invt/inventory/${id}/adjust/`, {
      quantity: String(direction * Number(size)),
      reason: text(form, "reason") || "COUNT",
      note: text(form, "note"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/stock");
  revalidatePath("/reports");
  return { field: {}, general: [] };
}

/**
 * Put a product on a branch's shelf for the first time.
 *
 * A product in the catalogue is not yet stock anywhere — BranchInventory is
 * per branch, which is what makes "we have 40 at Westlands and none at Karen"
 * expressible at all. Created at zero, then booked in, so the arrival has a
 * movement like every other.
 */
export async function stockAProduct(
  _previous: State,
  form: FormData,
): Promise<State> {
  const branch = Number(text(form, "branch"));
  const product = Number(text(form, "product"));
  if (!branch || !product) {
    return { field: {}, general: ["Choose a branch and a product."] };
  }

  try {
    await post("/invt/inventory/", {
      branch,
      product,
      quantity: "0",
      reorder_level: text(form, "reorder_level") || "0",
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/stock");
  return { field: {}, general: ["Added. Book the first delivery in below."] };
}
