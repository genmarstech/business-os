/**
 * Where a new business has got to, and what it needs next.
 *
 * ── THE STATE IS DERIVED, NEVER STORED ─────────────────────────────────────
 *
 * There is no `onboarding_step` column and there must not be one. A stored
 * step is a second source of truth that drifts the first time somebody
 * deletes a branch, adds one through the API, or abandons the flow halfway
 * and comes back a week later — and it drifts silently, leaving people stuck
 * on a screen telling them to do something they have already done.
 *
 * So the answer is computed from what exists. Four things have to exist before
 * a till can take a sale, and each genuinely depends on the one before it: a
 * branch needs a business, a register needs a branch, a sale needs stock and
 * somebody to ring it up.
 */

import "server-only";

import { getOrNull } from "./api";
import type { Me } from "./session";

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

export type Step = "business" | "branch" | "catalogue" | "register" | "done";

export type Progress = {
  next: Step;
  business: boolean;
  branches: number;
  products: number;
  registers: number;
  staff: number;
  taxRules: number;
};

/**
 * What this caller has, in one pass.
 *
 * Every call is scoped by the server to their tenant, so these counts are the
 * caller's own and nobody else's — see identity/scoping.py. A caller with no
 * membership gets 403 on most of them, which `getOrNull` turns into an empty
 * list; that is the correct answer for somebody who has no business yet.
 */
export async function progress(me: Me | null): Promise<Progress> {
  const hasBusiness =
    me?.kind === "subscriber"
      ? me.organisations.length > 0
      : Boolean(me?.organisation);

  if (!hasBusiness) {
    return {
      next: "business",
      business: false,
      branches: 0,
      products: 0,
      registers: 0,
      staff: 0,
      taxRules: 0,
    };
  }

  const [branches, products, registers, staff, taxRules] = await Promise.all([
    getOrNull<Page<unknown>>("/brn/branch/"),
    getOrNull<Page<unknown>>("/ctl/products/"),
    getOrNull<Page<unknown>>("/brn/register/"),
    getOrNull<Page<unknown>>("/org/staff/"),
    getOrNull<Page<unknown>>("/sls/tax-rules/"),
  ]);

  const counts = {
    business: true,
    branches: rows(branches).length,
    products: rows(products).length,
    registers: rows(registers).length,
    staff: rows(staff).length,
    taxRules: rows(taxRules).length,
  };

  // The order is the dependency order, not a preference. Each step needs the
  // one above it to exist.
  const next: Step =
    counts.branches === 0
      ? "branch"
      : counts.products === 0
        ? "catalogue"
        : counts.registers === 0
          ? "register"
          : "done";

  return { ...counts, next };
}
