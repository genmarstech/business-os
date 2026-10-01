"use server";

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

/**
 * Ending and resuming the arrangement.
 *
 * ── THERE IS NO ACTION HERE THAT PAYS FOR ANYTHING ────────────────────────
 * Extending a subscription is not an endpoint this application exposes, so
 * it cannot be an action here either. The money lives at Genmars, where the
 * invoice is; an endpoint in the POS that moved `paid_until` would be one
 * that granted entitlement with no payment behind it, and a subscriber's own
 * session would be enough to use it.
 */

export type State = FormErrors | null;

export async function cancelSubscription(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(String(form.get("id") ?? "").trim());
  if (!id) return { field: {}, general: ["No subscription to cancel."] };

  try {
    await post(`/sub/subscription/${id}/cancel/`, {
      reason: String(form.get("reason") ?? "").trim(),
    });
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

export async function reinstateSubscription(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(String(form.get("id") ?? "").trim());
  if (!id) return { field: {}, general: ["No subscription to reinstate."] };

  try {
    await post(`/sub/subscription/${id}/reinstate/`, {});
  } catch (error) {
    return asFormErrors(error);
  }

  refresh();
  return { field: {}, general: [] };
}

function refresh() {
  revalidatePath("/settings/subscription");
  // The banner is drawn from /auth/me in the shell, on every page. Without
  // this it keeps saying "cancelled" on every other screen until something
  // else happens to revalidate.
  revalidatePath("/", "layout");
}
