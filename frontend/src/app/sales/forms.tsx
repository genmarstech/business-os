"use client";

import { useActionState, useState } from "react";

import { General, Submit, Text } from "@/components/Form";
import { refundSale, reprint, voidSale, type State } from "./actions";
import type { Sale } from "./shape";
import styles from "./sales.module.css";

const NONE: State = null;

/**
 * Cancelling a sale.
 *
 * ── IT ASKS TWICE, AND THE SECOND TIME IT ASKS WHY ─────────────────────────
 * A void puts stock back and takes money out of the day's figures. The reason
 * is the only record of what happened, so the field is required and the
 * button is not reachable in one click from a list.
 */
export function VoidForm({ sale }: { sale: Sale }) {
  const [state, action] = useActionState(voidSale, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.danger} onClick={() => setOpen(true)}>
        Cancel this sale
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="sale_id" value={sale.id} />
      <General messages={state?.general ?? []} />

      <p className={styles.warnText}>
        This puts {sale.items.length} line
        {sale.items.length === 1 ? "" : "s"} of stock back and removes the sale
        from today&rsquo;s takings. It cannot be undone — if money has already
        changed hands, refund it instead.
      </p>

      <Text
        name="reason"
        label="Why"
        placeholder="Rung up twice"
        required
        autoFocus
        error={state?.field.reason}
      />

      <Submit pending="Cancelling…">Cancel the sale</Submit>
    </form>
  );
}

/**
 * Giving money back.
 *
 * Per line and per quantity, because a customer returning one of three is the
 * ordinary case. `restock` is ticked by default — most returns go back on the
 * shelf — and untickable, because a damaged item counted back in is stock the
 * shop does not have.
 */
export function RefundForm({
  sale,
  staffId,
}: {
  sale: Sale;
  staffId: number | null;
}) {
  const [state, action] = useActionState(refundSale, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        Refund something
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="sale_id" value={sale.id} />
      <input type="hidden" name="branch" value={sale.branch} />
      <input type="hidden" name="processed_by" value={staffId ?? ""} />
      <General messages={state?.general ?? []} />

      <table className={styles.refundTable}>
        <thead>
          <tr>
            <th>Line</th>
            <th className={styles.num}>Sold</th>
            <th className={styles.num}>Coming back</th>
            <th>Back on the shelf</th>
          </tr>
        </thead>
        <tbody>
          {sale.items.map((item) => (
            <tr key={item.id}>
              <td>
                <div className={styles.name}>{item.product_name}</div>
                <div className={styles.meta}>{item.sku}</div>
              </td>
              <td className={styles.num}>{Number(item.quantity)}</td>
              <td className={styles.num}>
                <input
                  className={styles.qtyInput}
                  name={`quantity_${item.id}`}
                  inputMode="decimal"
                  defaultValue=""
                  placeholder="0"
                  max={Number(item.quantity)}
                  aria-label={`How many ${item.product_name} are coming back`}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  name={`restock_${item.id}`}
                  defaultChecked
                  aria-label={`Put ${item.product_name} back into stock`}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <Text
        name="reason"
        label="Why"
        placeholder="Wrong size"
        required
        error={state?.field.reason}
      />

      <Submit pending="Refunding…">Give the money back</Submit>
    </form>
  );
}

export function ReprintForm({ sale }: { sale: Sale }) {
  const [state, action] = useActionState(reprint, NONE);

  return (
    <form action={action} className={styles.inline}>
      <input type="hidden" name="sale_id" value={sale.id} />
      <General messages={state?.general ?? []} />
      <button className={styles.quiet} type="submit">
        Reprint the receipt
      </button>
    </form>
  );
}
