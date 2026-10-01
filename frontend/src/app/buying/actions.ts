"use server";

/**
 * Buying, from the screen's side.
 *
 * ── NOTHING HERE DECIDES ANYTHING ──────────────────────────────────────────
 *
 * Each of these shapes a form into the payload an endpoint expects and hands
 * back whatever the server said. The rules — who may approve, what may still
 * be edited, how much is still outstanding — live in
 * `backend/procurement/services.py` and are checked there on every call. A
 * copy of any of them here would be a second opinion, and the two would
 * eventually disagree.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

function refresh() {
  revalidatePath("/buying");
  // A delivery moves stock, so the stock screens are now out of date too.
  revalidatePath("/stock");
  revalidatePath("/stock/history");
}

/**
 * Raise a draft order.
 *
 * The line fields arrive as parallel arrays — `product[]`, `quantity[]`,
 * `unit_cost[]` — because that is what a form with a repeating row posts.
 * Rows with no product are skipped rather than refused: an empty spare row at
 * the bottom of the form is how people use it.
 */
export async function raiseOrder(
  _previous: State,
  form: FormData,
): Promise<State> {
  const products = form.getAll("product").map(String);
  const quantities = form.getAll("quantity").map(String);
  const costs = form.getAll("unit_cost").map(String);

  const items = products
    .map((product, index) => ({
      product: Number(product),
      quantity_ordered: (quantities[index] ?? "").trim(),
      unit_cost: (costs[index] ?? "").trim(),
    }))
    .filter((line) => line.product && line.quantity_ordered)
    .map((line) =>
      line.unit_cost
        ? line
        : // Omitted entirely rather than sent empty: the server falls back to
          // the product's own cost, and "" is not a number it can parse.
          { product: line.product, quantity_ordered: line.quantity_ordered },
    );

  if (!items.length) {
    return { field: {}, general: ["Add at least one product and a quantity."] };
  }

  const expected = text(form, "expected_at");

  try {
    await post("/prc/purchase-orders/", {
      branch: Number(text(form, "branch")),
      supplier: Number(text(form, "supplier")),
      ...(expected ? { expected_at: expected } : {}),
      note: text(form, "note"),
      items,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: ["Raised as a draft. Send it when it is right."] };
}

export async function sendOrder(_previous: State, form: FormData): Promise<State> {
  return act(form, "submit");
}

export async function approveOrder(
  _previous: State,
  form: FormData,
): Promise<State> {
  return act(form, "approve");
}

export async function cancelOrder(
  _previous: State,
  form: FormData,
): Promise<State> {
  return act(form, "cancel", { reason: text(form, "reason") });
}

async function act(
  form: FormData,
  what: "submit" | "approve" | "cancel",
  body: Record<string, unknown> = {},
): Promise<State> {
  const id = Number(text(form, "order_id"));
  if (!id) return { field: {}, general: ["No order."] };

  try {
    await post(`/prc/purchase-orders/${id}/${what}/`, body);
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

/**
 * Book in a delivery.
 *
 * ⚠ THE IDEMPOTENCY KEY IS NOT OPTIONAL HERE, AND IT IS ON THE RAISE FORM.
 *
 * A double-tapped "Raise order" makes a second draft, which somebody cancels.
 * A double-booked delivery is stock the shop believes in and the shelf does
 * not — found weeks later by a stock take and usually blamed on theft. The
 * form mints a key per attempt and the server returns the first receipt for a
 * repeat, so a retry on a bad connection at the goods door costs nothing.
 */
export async function receiveDelivery(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "order_id"));
  const itemIds = form.getAll("item").map(String);
  const quantities = form.getAll("received").map(String);

  const lines = itemIds
    .map((item, index) => ({
      item: Number(item),
      quantity: (quantities[index] ?? "").trim(),
    }))
    // A line nobody counted is a line that did not arrive on this lorry, not
    // a zero to be recorded.
    .filter((line) => line.item && line.quantity && Number(line.quantity) > 0);

  if (!lines.length) {
    return { field: {}, general: ["Enter how many of something arrived."] };
  }

  try {
    await post(`/prc/purchase-orders/${id}/receive/`, {
      lines,
      delivery_note: text(form, "delivery_note"),
      note: text(form, "note"),
      idempotency_key: text(form, "idempotency_key"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: ["Booked in. The stock has moved."] };
}

export async function addSupplier(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organisation"));
  if (!organisation) {
    return { field: {}, general: ["No business to file this under."] };
  }

  try {
    await post("/prc/suppliers/", {
      organization: organisation,
      name: text(form, "name"),
      contact_person: text(form, "contact_person"),
      phone_number: text(form, "phone_number"),
      email: text(form, "email"),
      address: text(form, "address"),
      lead_time_days: Number(text(form, "lead_time_days") || "0"),
      note: text(form, "note"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/buying/suppliers");
  revalidatePath("/buying");
  return { field: {}, general: ["Added."] };
}

/**
 * Archive or restore a supplier.
 *
 * There is no delete. A supplier with orders behind it is PROTECTed at the
 * database, and an archived one still explains every order that names it —
 * which is the whole reason the order copies a name rather than joining to
 * one.
 */
export async function setSupplierActive(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "supplier_id"));
  if (!id) return { field: {}, general: ["No supplier."] };

  try {
    await patch(`/prc/suppliers/${id}/`, {
      is_active: text(form, "active") === "yes",
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/buying/suppliers");
  return { field: {}, general: [] };
}
