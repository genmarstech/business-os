"use server";

/**
 * Closing a till against a counted drawer.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE COUNT IS THE POINT, NOT THE CLOSING.
 *
 * A shift used to end by setting a field — no count, no close time, and so no
 * variance, which is the one number a till exists to produce. The server now
 * refuses that path entirely: `status` is not writable and the only way a
 * shift ends is POST .../close/ with what was actually in the drawer.
 *
 * Nothing here computes the variance. It comes back from the server, which
 * derives it from immutable completed sales — see branches/services.py. A
 * figure somebody will argue about should be produced once, where the money
 * is, not in a browser.
 * ══════════════════════════════════════════════════════════════════════════
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";
import type { SafePayload } from "@/lib/reports";

export type Drawer = {
  opening_cash: string;
  /** What crossed the counter, gross — `change_given` is what went back. */
  cash_taken: string;
  change_given: string;
  /*
   * Cash moved in or out mid-shift, and returns paid out of the drawer.
   *
   * Carried here because the close result is the one moment a manager is
   * looking straight at a variance and asking where it came from. The server
   * has always returned these; nothing showed them, so the breakdown stopped
   * at "taken less change" and a banked float looked like a shortfall with no
   * cause on the screen.
   */
  paid_in: string;
  paid_out: string;
  refunded_cash: string;
  expected_cash: string;
  counted_cash: string | null;
  /** Positive is over, negative is short. Null until somebody counts. */
  variance: string | null;
};

export type State = (FormErrors & { drawer?: Drawer }) | null;

export async function closeTill(
  _previous: State,
  form: FormData,
): Promise<State> {
  const shift = Number(String(form.get("shift_id") ?? "").trim());
  const counted = String(form.get("counted_cash") ?? "").trim();

  if (!shift) return { field: {}, general: ["No till to close."] };
  if (counted === "") {
    return {
      field: { counted_cash: "Count the drawer and enter what is in it." },
      general: [],
    };
  }

  let result: { drawer: Drawer };
  try {
    result = await post<{ drawer: Drawer }>(
      `/brn/register-shifts/${shift}/close/`,
      { counted_cash: counted },
    );
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/reports");
  revalidatePath("/");
  return { field: {}, general: [], drawer: result.drawer };
}

/**
 * The safe — the other end of a SAFE_DROP.
 *
 * Both actions return the whole payload rather than just the row that
 * changed, for the same reason `closeTill` returns the whole drawer: the
 * balance, the pending list and the ledger are one screen's worth of state,
 * and a caller re-deriving one from the other is a second implementation of
 * arithmetic the server already did once.
 */
export type SafeState = (FormErrors & { payload?: SafePayload }) | null;

export async function receiveDrop(
  _previous: SafeState,
  form: FormData,
): Promise<SafeState> {
  const branchId = Number(String(form.get("branch_id") ?? "").trim());
  const cashMovementId = String(form.get("cash_movement_id") ?? "").trim();

  if (!branchId || !cashMovementId) {
    return { field: {}, general: ["Nothing to receive."] };
  }

  let payload: SafePayload;
  try {
    payload = await post<SafePayload>(`/brn/branch/${branchId}/safe/receive/`, {
      cash_movement_id: cashMovementId,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/reports");
  return { field: {}, general: [], payload };
}

export async function bankSafe(
  _previous: SafeState,
  form: FormData,
): Promise<SafeState> {
  const branchId = Number(String(form.get("branch_id") ?? "").trim());
  const amount = String(form.get("amount") ?? "").trim();
  const reference = String(form.get("reference") ?? "").trim();
  const note = String(form.get("note") ?? "").trim();

  if (!branchId) return { field: {}, general: ["No branch to bank for."] };

  let payload: SafePayload;
  try {
    payload = await post<SafePayload>(`/brn/branch/${branchId}/safe/bank/`, {
      amount,
      reference,
      note,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/reports");
  return { field: {}, general: [], payload };
}
