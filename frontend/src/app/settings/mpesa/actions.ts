"use server";

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, post, type FormErrors } from "@/lib/api";

/**
 * Saving the shop's own M-Pesa configuration.
 *
 * ── A BLANK SECRET MEANS "LEAVE IT", NOT "CLEAR IT" ───────────────────────
 * The form cannot show what is stored — the API never returns it — so it
 * submits blanks for anything untouched. The server reads a blank the same
 * way. Sending an empty string as a deletion would wipe the passkey every
 * time somebody corrected a typo in the short code.
 */

export type State = FormErrors | null;

function text(form: FormData, name: string): string {
  return String(form.get(name) ?? "").trim();
}

export async function saveMpesaTill(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "id"));
  const organization = Number(text(form, "organization"));
  /*
   * Empty means the business default. Sent as null rather than omitted so
   * an existing row can be moved back to being the default — omitting it
   * would leave whatever branch it is already attached to.
   */
  const branch = Number(text(form, "branch")) || null;

  const body = {
    ...(organization ? { organization } : {}),
    branch,
    environment: text(form, "environment") || "sandbox",
    short_code: text(form, "short_code"),
    // Both are sent on every save, including as empty strings. The field is
    // hidden rather than preserved when the kind changes, so a paybill must
    // be able to clear a store number left behind by a buy-goods config —
    // and the reverse. These are numbers printed on a shop's wall, not
    // secrets, so the "blank means leave it" rule below does not apply.
    store_number: text(form, "store_number"),
    transaction_type: text(form, "transaction_type"),
    account_reference: text(form, "account_reference"),
    is_active: form.get("is_active") === "on",
    consumer_key: text(form, "consumer_key"),
    consumer_secret: text(form, "consumer_secret"),
    passkey: text(form, "passkey"),
  };

  try {
    if (id) {
      await patch(`/pay/mpesa/till/${id}/`, body);
    } else {
      await post("/pay/mpesa/till/", body);
    }
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/mpesa");
  // The till refuses to offer M-Pesa until this is on.
  revalidatePath("/till");
  return { field: {}, general: [] };
}
