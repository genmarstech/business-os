"use server";

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, post, type FormErrors } from "@/lib/api";

/**
 * Writing price lists.
 *
 * ── THE PRICES ARE SENT WHOLE, NOT ONE AT A TIME ──────────────────────────
 * `entries` replaces what is on the list. That is what makes removing a
 * product from a promotion possible — the operation somebody needs on the
 * morning it was supposed to end, and the one a merge-only API cannot do.
 */

export type State = FormErrors | null;

/** Line fields arrive as parallel arrays: one `product` and one `price` per row. */
function entriesFrom(form: FormData) {
  const products = form.getAll("product").map(String);
  const prices = form.getAll("price").map(String);
  return products
    .map((product, index) => ({
      product: Number(product),
      price: (prices[index] ?? "").trim(),
    }))
    .filter((entry) => entry.product && entry.price !== "");
}

export async function savePriceList(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(String(form.get("id") ?? "").trim());
  const organization = Number(String(form.get("organization") ?? "").trim());

  const body = {
    ...(organization ? { organization } : {}),
    name: String(form.get("name") ?? "").trim(),
    note: String(form.get("note") ?? "").trim(),
    precedence: Number(String(form.get("precedence") ?? "0").trim()),
    // Empty means "no end", which is different from an empty string the
    // serializer would reject as an invalid date.
    starts_on: String(form.get("starts_on") ?? "").trim() || null,
    ends_on: String(form.get("ends_on") ?? "").trim() || null,
    branches: form.getAll("branch").map((value) => Number(value)),
    entries: entriesFrom(form),
  };

  try {
    if (id) {
      await patch(`/ctl/price-lists/${id}/`, body);
    } else {
      await post("/ctl/price-lists/", body);
    }
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

export async function setListActive(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(String(form.get("id") ?? "").trim());
  if (!id) return { field: {}, general: ["No list."] };

  try {
    await patch(`/ctl/price-lists/${id}/`, {
      is_active: String(form.get("is_active")) === "true",
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

function refresh() {
  revalidatePath("/catalogue/prices");
  // The till reads resolved prices, and the catalogue shows the base beside
  // them. Both go stale the moment a list changes.
  revalidatePath("/catalogue");
  revalidatePath("/till");
}
