"use server";

/**
 * Catalogue writes.
 *
 * ── A PRICE CHANGE IS NOT RETROACTIVE, AND NOTHING HERE MAKES IT ONE ───────
 *
 * Every sale copies the price and the tax rate it was charged at onto its own
 * line — sales/models.py keeps them as columns, not as a join. So editing a
 * product here changes what the NEXT sale charges and rewrites no receipt that
 * has already been issued. That is the one property a shopkeeper must be able
 * to rely on when they put prices up, and it is worth knowing that it is the
 * backend that guarantees it, not this file.
 */

import { revalidatePath } from "next/cache";

import { asFormErrors, patch, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

const ok: FormErrors = { field: {}, general: [] };

export async function saveProduct(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  const id = Number(text(form, "product_id")) || null;
  if (!organisation) {
    return { field: {}, general: ["No business to add a product to."] };
  }

  const body = {
    organization: organisation,
    category: Number(text(form, "category")) || null,
    name: text(form, "name"),
    description: text(form, "description"),
    sku: text(form, "sku"),
    barcode: text(form, "barcode"),
    cost_price: text(form, "cost_price") || "0",
    selling_price: text(form, "selling_price"),
    tax_rule: Number(text(form, "tax_rule")) || null,
    is_active: text(form, "is_active") !== "false",
  };

  try {
    if (id) {
      await patch(`/ctl/products/${id}/`, body);
    } else {
      const made = await post<{ id: number }>("/ctl/products/", body);

      /*
       * A new product that is on no shelf cannot be sold, and the till's
       * refusal names the branch rather than the omission. Stocking it here
       * — at zero if they did not say — at least puts it on /stock where the
       * count can be booked in.
       */
      const branch = Number(text(form, "stock_branch"));
      if (branch) {
        await post(`/ctl/products/${made.id}/stock/`, {
          branch,
          quantity: text(form, "opening_stock") || "0",
          note: "Opening stock",
        });
      }
    }
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/catalogue");
  revalidatePath("/");
  return ok;
}

export async function addCategory(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  if (!organisation) return { field: {}, general: ["No business."] };

  try {
    await post("/ctl/categories/", {
      organization: organisation,
      name: text(form, "name"),
      description: text(form, "description"),
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/catalogue");
  return ok;
}

/**
 * Add or change a tax rule.
 *
 * ⚠ CHANGING A RATE DOES NOT RE-TAX ANYTHING SOLD. Every sale copied the rate
 *   it charged, so raising VAT today leaves yesterday's receipts saying what
 *   they said — which is what a tax authority expects and the opposite of what
 *   somebody editing a single number here might assume. The screen says so.
 */
export async function saveTaxRule(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  const id = Number(text(form, "rule_id")) || null;
  if (!organisation) return { field: {}, general: ["No business."] };

  const body = {
    organization: organisation,
    name: text(form, "name"),
    rate: text(form, "rate"),
    // Kenyan shelf prices are normally VAT-inclusive. The checkbox is checked
    // by default and this reads its absence as false, which is what an
    // unchecked box sends — nothing.
    is_inclusive: form.get("is_inclusive") !== null,
    is_default: form.get("is_default") !== null,
    is_active: text(form, "is_active") !== "false",
  };

  try {
    if (id) {
      await patch(`/sls/tax-rules/${id}/`, body);
    } else {
      await post("/sls/tax-rules/", body);
    }
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/catalogue");
  revalidatePath("/settings/tax");
  return ok;
}
