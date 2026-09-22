"use server";

/**
 * Who administers this business.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THIS IS THE SUBSCRIBER TIER, NOT THE TILL TIER. THEY ARE NOT THE SAME LIST.
 *
 *   /staff            cashiers and branch managers. Credentials that belong
 *                     to this business and never reach Genmars.
 *   here              owners, admins and accountants. People who deal with
 *                     Genmars and sign in with a Genmars account.
 *
 * CLAUDE.md draws that line by who the commercial relationship is with, and
 * the two tiers have separate credential stores that never cross. A screen
 * that merged them would be the first place somebody assumed a cashier could
 * be promoted into an administrator.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── AN INVITATION GRANTS NOTHING WHEN IT IS SENT ──────────────────────────
 *
 * It is an offer matched against an address Genmars has verified. The
 * membership appears the moment that person completes the sign-on handoff and
 * not before — so there is no link to leak and nothing to intercept.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

const ok: FormErrors = { field: {}, general: [] };

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

export async function invitePerson(
  _previous: State,
  form: FormData,
): Promise<State> {
  const email = text(form, "email");
  if (!email) {
    return { field: { email: "Which email address?" }, general: [] };
  }

  try {
    await post("/auth/invitations/", {
      email,
      role: text(form, "role") || "admin",
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/people");
  return {
    field: {},
    general: [
      `Invited ${email}. They join the moment they sign in with that Genmars account — tell them it is waiting.`,
    ],
  };
}

export async function withdrawInvitation(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "invitation_id"));
  if (!id) return { field: {}, general: ["Nothing to withdraw."] };

  try {
    await post(`/auth/invitations/${id}/revoke/`, {});
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/people");
  return ok;
}

export async function changeRole(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "membership_id"));
  const role = text(form, "role");
  if (!id || !role) return { field: {}, general: ["Nothing to change."] };

  try {
    await post(`/auth/members/${id}/set-role/`, { role });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/people");
  revalidatePath("/", "layout");
  return ok;
}

/**
 * Take somebody out of the business.
 *
 * Their Genmars account is untouched — it is not ours to disable, and they
 * may well administer another shop with it. The server refuses to remove the
 * last owner, because a business nobody can administer cannot be recovered by
 * anybody: the authority to recover it is the thing that was removed.
 */
export async function removePerson(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "membership_id"));
  if (!id) return { field: {}, general: ["Nobody to remove."] };

  try {
    await post(`/auth/members/${id}/remove/`, {});
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/settings/people");
  return { field: {}, general: ["Removed. Their Genmars account is untouched."] };
}
