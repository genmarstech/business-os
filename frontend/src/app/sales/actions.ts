"use server";

/**
 * Undoing a sale, the two ways there are.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * VOID AND REFUND ARE NOT THE SAME THING AND MUST NOT LOOK LIKE IT.
 *
 *   void    the sale should not have happened. The stock goes back, the
 *           takings never counted, and it stops appearing as revenue.
 *   refund  the sale happened and money is going back. Both records stand,
 *           the refund is its own row, and the day's figures show each.
 *
 * Blueprint §10: transaction history is immutable. Neither of these edits the
 * original sale — a void marks it voided, a refund writes a new row beside
 * it. A shop that could quietly alter a completed sale has no audit trail at
 * all, which is the one thing a till exists to produce.
 * ══════════════════════════════════════════════════════════════════════════
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

export async function voidSale(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "sale_id"));
  const reason = text(form, "reason");
  if (!id) return { field: {}, general: ["No sale to cancel."] };
  if (!reason) {
    return {
      field: { reason: "Say why. It is the only record of what happened." },
      general: [],
    };
  }

  try {
    await post(`/sls/sales/${id}/void/`, { reason });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/sales");
  revalidatePath("/reports");
  return { field: {}, general: ["Cancelled. The stock has gone back."] };
}

/**
 * Give money back against a sale.
 *
 * `restock` defaults to true because most returns go back on the shelf — but
 * not all do, and a damaged item counted back into stock is stock the shop
 * does not have. The form asks per line.
 */
export async function refundSale(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "sale_id"));
  const branch = Number(text(form, "branch"));
  const processedBy = Number(text(form, "processed_by"));
  const reason = text(form, "reason");

  if (!id || !branch) return { field: {}, general: ["No sale to refund."] };
  if (!processedBy) {
    return {
      field: {},
      general: [
        "A refund has to be recorded against a member of staff. Add one under Staff first.",
      ],
    };
  }
  if (!reason) {
    return {
      field: { reason: "Say why the money is going back." },
      general: [],
    };
  }

  const lines: { sale_item: number; quantity: string; restock: boolean }[] = [];
  for (const [key, value] of form.entries()) {
    const match = /^quantity_(\d+)$/.exec(key);
    if (!match) continue;
    const quantity = String(value).trim();
    if (!quantity || Number(quantity) <= 0) continue;
    lines.push({
      sale_item: Number(match[1]),
      quantity,
      restock: form.get(`restock_${match[1]}`) !== null,
    });
  }

  if (lines.length === 0) {
    return {
      field: {},
      general: ["Choose how much of what is coming back."],
    };
  }

  try {
    await post(`/sls/sales/${id}/refund/`, {
      branch,
      processed_by: processedBy,
      reason,
      lines,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/sales");
  revalidatePath("/reports");
  return { field: {}, general: ["Refunded."] };
}

export async function reprint(
  _previous: State,
  form: FormData,
): Promise<State> {
  const id = Number(text(form, "sale_id"));
  if (!id) return { field: {}, general: ["No sale."] };

  try {
    await post(`/sls/sales/${id}/reprint/`, {});
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/sales");
  return {
    field: {},
    general: ["Reprinted. The copy is marked as one — see the receipt."],
  };
}
