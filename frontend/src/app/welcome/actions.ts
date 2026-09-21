"use server";

/**
 * The onboarding writes.
 *
 * ── SERVER ACTIONS, NOT A CLIENT FETCH ─────────────────────────────────────
 *
 * The session cookie lives on this origin and the CSRF token is echoed from
 * it; doing that from the browser would mean shipping the same cookie
 * handling into a client bundle for no gain. A server action keeps every
 * credential on the server and gives progressive enhancement for free — these
 * forms submit and work with JavaScript disabled.
 *
 * Each one returns field errors rather than throwing, because a create that
 * fails validation is an ordinary outcome a form has to render, not an
 * exception.
 */

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { asFormErrors, post, type FormErrors } from "@/lib/api";

export type State = FormErrors | null;

function text(form: FormData, key: string): string {
  return String(form.get(key) ?? "").trim();
}

/**
 * Create the business itself — the one write in this application that needs
 * no permission.
 *
 * A subscriber who has just signed in holds no TenantMembership, therefore no
 * permission at all, and this is the act that gives them one. Gating it would
 * mean needing a membership to create the organisation that grants the
 * membership. The backend leaves it open for exactly this reason; see the
 * banner on BusinessOrganizationViewSet.
 */
export async function createBusiness(
  _previous: State,
  form: FormData,
): Promise<State> {
  const name = text(form, "name");
  if (!name) return { field: { name: "Give the business a name." }, general: [] };

  try {
    await post("/org/organizations/", {
      name,
      staff_size: text(form, "staff_size") || "MD",
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/", "layout");
  redirect("/welcome");
}

export async function createBranch(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  if (!organisation) {
    return { field: {}, general: ["No business to add a branch to."] };
  }

  try {
    await post("/brn/branch/", {
      // `organization_id` on write, `organization` nested on read. The
      // serialiser is shaped that way; this is not a typo.
      organization_id: organisation,
      branch_name: text(form, "branch_name"),
      branch_location: text(form, "branch_location"),
      branch_allocation: text(form, "branch_allocation") || "Main floor",
      branch_manager: text(form, "branch_manager"),
      // Branches default to inactive on the model. A branch somebody has just
      // created in an onboarding flow is one they intend to use, and leaving
      // it off would be a switch nobody knows to look for.
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/", "layout");
  redirect("/welcome");
}

/**
 * The first product, and the category and tax rule it needs.
 *
 * Three writes behind one form, because asking somebody to create a category
 * before they can create a product is an implementation detail leaking into
 * an onboarding screen. They are separate screens later, when there is a
 * catalogue to manage.
 *
 * ⚠ NOT ATOMIC. There is no endpoint that does all three, so a failure at the
 *   product leaves the category and the rule behind. Both are harmless and
 *   reused on the retry — `get_or_create` semantics by way of looking first —
 *   which is why this is acceptable here and would not be for a sale.
 */
export async function createFirstProduct(
  _previous: State,
  form: FormData,
): Promise<State> {
  const organisation = Number(text(form, "organization_id"));
  if (!organisation) {
    return { field: {}, general: ["No business to add a product to."] };
  }

  const rate = text(form, "tax_rate");

  try {
    let taxRuleId: number | null = Number(text(form, "tax_rule_id")) || null;

    if (!taxRuleId && rate) {
      const rule = await post<{ id: number }>("/sls/tax-rules/", {
        organization: organisation,
        name: `VAT ${rate}%`,
        rate,
        // Kenyan shelf prices are normally VAT-inclusive: the label says 116
        // and 16 of it is tax. The opposite would make every total jump at
        // the payment step.
        is_inclusive: true,
        is_default: true,
      });
      taxRuleId = rule.id;
    }

    let categoryId = Number(text(form, "category_id")) || null;
    if (!categoryId) {
      const category = await post<{ id: number }>("/ctl/categories/", {
        organization: organisation,
        name: text(form, "category_name") || "General",
      });
      categoryId = category.id;
    }

    await post("/ctl/products/", {
      organization: organisation,
      category: categoryId,
      name: text(form, "name"),
      sku: text(form, "sku"),
      barcode: text(form, "barcode"),
      cost_price: text(form, "cost_price") || "0",
      selling_price: text(form, "selling_price"),
      tax_rule: taxRuleId,
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/", "layout");
  redirect("/welcome");
}

export async function createRegister(
  _previous: State,
  form: FormData,
): Promise<State> {
  const branch = Number(text(form, "branch_id"));
  if (!branch) {
    return { field: {}, general: ["Choose a branch for this register."] };
  }

  try {
    await post("/brn/register/", {
      branch_id: branch,
      name: text(form, "name"),
      register_number: text(form, "register_number"),
      is_active: true,
    });
  } catch (error) {
    return asFormErrors(error);
  }

  revalidatePath("/", "layout");
  redirect("/welcome");
}
