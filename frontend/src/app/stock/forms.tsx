"use client";

import { useActionState, useState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import { adjustStock, setReorder, stockAProduct, type State } from "./actions";
import styles from "./stock.module.css";

const NONE: State = null;

/** Mirrors inventory/services.REASONS. A reason the server rejects is a typo. */
const REASONS = [
  { value: "DELIVERY", label: "Stock arrived", direction: "in" },
  { value: "COUNT", label: "A count disagreed", direction: "either" },
  { value: "DAMAGE", label: "Damaged or expired", direction: "out" },
  { value: "THEFT", label: "Missing", direction: "out" },
  { value: "RETURN", label: "Sent back to a supplier", direction: "out" },
  { value: "OTHER", label: "Something else", direction: "either" },
];

export function AdjustForm({
  inventoryId,
  productName,
  onHand,
}: {
  inventoryId: number;
  productName: string;
  onHand: string;
}) {
  const [state, action] = useActionState(adjustStock, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        Adjust
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="inventory_id" value={inventoryId} />
      <General messages={state?.general ?? []} />

      <p className={styles.formLede}>
        {productName} — {onHand} on hand. Say how many are moving, not what the
        new total should be: the difference is what leaves a record anybody can
        follow.
      </p>

      <Row>
        <Select name="direction" label="Which way" defaultValue="in">
          <option value="in">Coming in</option>
          <option value="out">Going out</option>
        </Select>
        <Text
          name="quantity"
          label="How many"
          inputMode="decimal"
          mono
          required
          autoFocus
          error={state?.field.quantity}
        />
      </Row>

      <Select
        name="reason"
        label="Why"
        defaultValue="DELIVERY"
        error={state?.field.reason}
      >
        {REASONS.map((reason) => (
          <option key={reason.value} value={reason.value}>
            {reason.label}
          </option>
        ))}
      </Select>

      <Text
        name="note"
        label="Note"
        hint="Optional, and the only thing anybody reading this next month will have."
        placeholder="Friday delivery, 2 crates"
        error={state?.field.note}
      />

      <Submit pending="Recording…">Record it</Submit>
    </form>
  );
}

export function StockAProductForm({
  branches,
  products,
}: {
  branches: { id: number; branch_name: string }[];
  products: { id: number; name: string; sku: string }[];
}) {
  const [state, action] = useActionState(stockAProduct, NONE);

  return (
    <form action={action} className={styles.form}>
      <General messages={state?.general ?? []} />

      <Row>
        <Select name="branch" label="Branch" required error={state?.field.branch}>
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>
        <Select
          name="product"
          label="Product"
          required
          error={state?.field.product}
        >
          {products.map((product) => (
            <option key={product.id} value={product.id}>
              {product.name} — {product.sku}
            </option>
          ))}
        </Select>
      </Row>

      <Row>
        <Text
          name="reorder_level"
          label="Tell me when it drops to"
          hint="What counts as running low here. Leave at 0 for no warning."
          inputMode="decimal"
          mono
          defaultValue="0"
          error={state?.field.reorder_level}
        />
        <Text
          name="reorder_quantity"
          label="And order this many"
          hint="Drafts an order automatically when it does — only if the product also names a supplier to auto-order from. Leave at 0 to never draft one."
          inputMode="decimal"
          mono
          defaultValue="0"
          error={state?.field.reorder_quantity}
        />
      </Row>

      <Submit pending="Adding…">Put it on the shelf</Submit>
    </form>
  );
}

/**
 * Change an existing line's reorder settings.
 *
 * ── THESE TWO FIELDS HAD NO WAY TO BE CHANGED AFTER A PRODUCT WAS FIRST
 *    STOCKED, AND THAT WAS ALREADY TRUE OF `reorder_level` BEFORE THIS
 *    EXISTED ──
 * `StockAProductForm` above only ever asked once, at the moment a product
 * first went on a shelf. A shop that wants to raise or lower either figure
 * later had no screen for it. This is that screen, following the same
 * open/close pattern as `AdjustForm` beside it.
 */
export function ReorderForm({
  inventoryId,
  productName,
  reorderLevel,
  reorderQuantity,
}: {
  inventoryId: number;
  productName: string;
  reorderLevel: string;
  reorderQuantity: string;
}) {
  const [state, action] = useActionState(setReorder, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        Set reorder
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="inventory_id" value={inventoryId} />
      <General messages={state?.general ?? []} />

      <p className={styles.formLede}>{productName}</p>

      <Row>
        <Text
          name="reorder_level"
          label="Tell me when it drops to"
          hint="Leave at 0 for no warning."
          inputMode="decimal"
          mono
          defaultValue={reorderLevel}
          error={state?.field.reorder_level}
        />
        <Text
          name="reorder_quantity"
          label="And order this many"
          hint="Needs the product to also name a supplier to auto-order from. Leave at 0 to never draft one."
          inputMode="decimal"
          mono
          defaultValue={reorderQuantity}
          error={state?.field.reorder_quantity}
        />
      </Row>

      <Submit pending="Saving…">Save</Submit>
    </form>
  );
}
