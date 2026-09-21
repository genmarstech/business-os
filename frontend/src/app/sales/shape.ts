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
