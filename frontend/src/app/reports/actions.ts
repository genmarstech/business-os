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

export type Drawer = {
  opening_cash: string;
  cash_taken: string;
  change_given: string;
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
