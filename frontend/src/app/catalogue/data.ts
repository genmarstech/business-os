import "server-only";

import { getOrNull } from "@/lib/api";

export type { Category, Product, TaxRule } from "./shape";

import type { Category, Product, TaxRule } from "./shape";

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

/**
 * The catalogue, its categories and the tax rules prices are quoted against.
 *
 * Three scoped lists rather than one expanded response, for the same reason as
 * the staff board: each is already confined to the caller's tenant by the
 * server, and joining here cannot widen that.
 */
export async function catalogue() {
  const [products, categories, taxRules] = await Promise.all([
    getOrNull<Page<Product>>("/ctl/products/"),
    getOrNull<Page<Category>>("/ctl/categories/"),
    getOrNull<Page<TaxRule>>("/sls/tax-rules/"),
  ]);

  return {
    products: rows(products),
    categories: rows(categories),
    taxRules: rows(taxRules),
  };
}
