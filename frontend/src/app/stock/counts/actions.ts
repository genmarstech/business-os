"use server";

/**
 * Taking a stock count, from the office.
 *
 * ── NOTHING HERE SAYS WHO IS DOING IT ──────────────────────────────────────
 *
 * There is no `opened_by`, no `counted_by`, no `closed_by`. The server signs
 * every one of these with the session the request arrived on, because an
 * attribution a client can state is not an attribution — see the banner on
 * `inventory/services._actor`. These functions shape a form into a payload
 * and hand back whatever the server said; every rule about what may be
 * counted, by whom, and when it may be closed lives in
 * `backend/inventory/services.py` and is checked there on every call.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

function refresh() {
  revalidatePath("/stock/counts");
}

export async function openCount(
  _previous: State,
  form: FormData,
): Promise<State> {
  const branch = Number(text(form, "branch"));
  if (!branch) return { field: { branch: "Which branch?" }, general: [] };

  try {
    await post("/invt/stock-counts/open/", {
      branch,
      note: text(form, "note"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

/**
 * Write down what is on one shelf.
 *
 * Posted one line at a time rather than as a sheet, which is the same
 * decision the till makes and for a weaker version of the same reason: a
 * tab left open for an hour while somebody walks the shop is a tab that can
 * be closed, and a sheet held in the browser until a Save button loses the
 * morning's work.
 */
export async function recordLine(
  _previous: State,
  form: FormData,
): Promise<State> {
  const count = Number(text(form, "count_id"));
  const inventory = Number(text(form, "inventory"));
  const counted = text(form, "counted");

  if (!count || !inventory) return { field: {}, general: ["No such shelf."] };
  if (counted === "") {
    return { field: { counted: "How many are there?" }, general: [] };
  }

  try {
    await post(`/invt/stock-counts/${count}/record/`, {
      inventory,
      counted,
      note: text(form, "note"),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

/**
 * Book every variance and close the count.
 *
 * ⚠ IRREVERSIBLE, AND THE SCREEN SAYS SO BEFORE THE BUTTON IS PRESSED.
 *
 * A count that can be reopened is a count whose variance means nothing,
 * because the second number is always the one that agrees. A miscount is
 * corrected by counting again — a new count, which is its own record.
 */
export async function closeCount(
  _previous: State,
  form: FormData,
): Promise<State> {
  const count = Number(text(form, "count_id"));
  if (!count) return { field: {}, general: ["No such count."] };

  try {
    await post(`/invt/stock-counts/${count}/close/`, {});
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  // Closing moves stock, so both stock screens are now out of date.
  revalidatePath("/stock");
  revalidatePath("/stock/history");
  return { field: {}, general: [] };
}

export async function abandonCount(
  _previous: State,
  form: FormData,
): Promise<State> {
  const count = Number(text(form, "count_id"));
  const reason = text(form, "reason");
  if (!count) return { field: {}, general: ["No such count."] };
  if (!reason) {
    return {
      field: { reason: "Say why this count is being abandoned." },
      general: [],
    };
  }

  try {
    await post(`/invt/stock-counts/${count}/abandon/`, { reason });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}
