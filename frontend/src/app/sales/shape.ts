export type SaleItem = {
  id: number;
  product: number;
  product_name: string;
  sku: string;
  unit_price: string;
  quantity: string;
  discount_amount: string;
  tax_rate: string;
  tax_amount: string;
  line_total: string;
  /**
   * What is left to return on this line, after every completed refund
   * already written against it.
   *
   * The refund form used to cap its quantity box at `quantity`, the figure
   * SOLD — so on a partly-returned sale it offered more than the server
   * would accept and answered with a refusal. The server derives this the
   * same way it enforces it; see SaleItemSerializer.
   */
  quantity_refunded?: string;
  quantity_refundable?: string;
};

export type PaymentRow = {
  id: number;
  method: string;
  method_label?: string;
  amount: string;
  reference?: string;
};

export type Sale = {
  id: number;
  number: string;
  status: "held" | "completed" | "voided" | string;
  status_label: string;
  branch: number;
  cashier: number;
  /**
   * Null for a walk-in. It decides whether a refund may go back "on
   * account": without a customer there is no balance to reduce, and the
   * server refuses that combination rather than writing the money off.
   */
  customer?: number | null;
  subtotal: string;
  discount_total: string;
  tax_total: string;
  total: string;
  amount_refunded: string;
  items: SaleItem[];
  payments: PaymentRow[];
  void_reason?: string;
  voided_at?: string | null;
  completed_at?: string | null;
  created_at: string;
};
