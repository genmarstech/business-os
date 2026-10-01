/**
 * What a price-list screen passes around.
 *
 * Client components read this, so nothing here fetches or touches a cookie.
 */

export type Entry = {
  id: number;
  product: number;
  product_name: string;
  product_sku: string;
  /** What it would cost without this list — shown beside the override. */
  base_price: string;
  price: string;
};

export type PriceList = {
  id: number;
  name: string;
  note: string;
  starts_on: string | null;
  ends_on: string | null;
  precedence: number;
  is_active: boolean;
  /**
   * In force TODAY, which `is_active` does not answer on its own — a
   * promotion can be active and three weeks away. Computed by the server so
   * there is one definition of "running".
   */
  in_force: boolean;
  branches: number[];
  entries: Entry[];
};

export type Branch = { id: number; branch_name: string; is_active: boolean };

export type Product = {
  id: number;
  name: string;
  sku: string;
  selling_price: string;
  is_active: boolean;
};
