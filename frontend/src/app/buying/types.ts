/**
 * The shapes the buying endpoints return.
 *
 * Mirrors `backend/procurement/serializers.py`. Money and quantities are
 * strings here for the reason lib/money.ts spells out: DRF's encoder turns a
 * Decimal into a float, and 19.99 arrives as 19.989999999999998. Nothing in
 * this section parses one.
 */

export type OrderStatus =
  | "draft"
  | "submitted"
  | "approved"
  | "part_received"
  | "received"
  | "cancelled";

export type OrderLine = {
  id: number;
  product: number;
  product_name: string;
  product_sku: string;
  quantity_ordered: string;
  quantity_received: string;
  outstanding: string;
  unit_cost: string;
  line_total: string;
};

export type Order = {
  id: number;
  branch: number;
  branch_name: string;
  supplier: number;
  supplier_name: string;
  number: number;
  status: OrderStatus;
  status_label: string;
  is_editable: boolean;
  expected_at: string | null;
  note: string;
  total: string;
  items: OrderLine[];
  raised_by_name: string;
  approved_by_name: string;
  approved_at: string | null;
  cancelled_at: string | null;
  cancelled_reason: string;
  created_at: string;
};

export type Supplier = {
  id: number;
  name: string;
  contact_person: string;
  phone_number: string;
  email: string;
  address: string;
  lead_time_days: number;
  note: string;
  is_active: boolean;
};

export type Branch = { id: number; branch_name: string; is_active: boolean };
export type Product = {
  id: number;
  name: string;
  sku: string;
  cost_price: string;
  is_active: boolean;
};

/** Open means something is still expected to happen to it. */
export const OPEN: OrderStatus[] = [
  "draft",
  "submitted",
  "approved",
  "part_received",
];
