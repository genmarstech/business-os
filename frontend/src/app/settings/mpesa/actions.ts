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

  const body = {
    ...(organization ? { organization } : {}),
    environment: text(form, "environment") || "sandbox",
    short_code: text(form, "short_code"),
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
