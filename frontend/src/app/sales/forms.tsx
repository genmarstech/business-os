"use client";

import { useActionState, useState } from "react";

import { General, Select, Submit, Text } from "@/components/Form";
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
            <th className={styles.num}>Can return</th>
            <th className={styles.num}>Coming back</th>
            <th>Back on the shelf</th>
          </tr>
        </thead>
        <tbody>
          {sale.items.map((item) => {
            /*
             * What is still returnable, not what was sold. Capping at the
             * sold quantity offered more than the server would take on a
             * partly-returned sale, and the only way to find out was a
             * refusal. Falls back to the sold figure when the API predates
             * the field — the server refuses an over-return either way.
             */
            const remaining = Number(
              item.quantity_refundable ?? item.quantity,
            );
            const spent = !(remaining > 0);
            return (
              <tr key={item.id}>
                <td>
                  <div className={styles.name}>{item.product_name}</div>
                  <div className={styles.meta}>{item.sku}</div>
                </td>
                <td className={styles.num}>{Number(item.quantity)}</td>
                <td className={styles.num}>
                  {spent ? (
                    <span className={styles.meta}>all returned</span>
                  ) : (
                    remaining
                  )}
                </td>
                <td className={styles.num}>
                  <input
                    className={styles.qtyInput}
                    name={`quantity_${item.id}`}
                    inputMode="decimal"
                    defaultValue=""
                    placeholder="0"
                    max={remaining}
                    disabled={spent}
                    aria-label={`How many ${item.product_name} are coming back`}
                  />
                </td>
                <td>
                  <input
                    type="checkbox"
                    name={`restock_${item.id}`}
                    defaultChecked
                    disabled={spent}
                    aria-label={`Put ${item.product_name} back into stock`}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <Text
        name="reason"
        label="Why"
        placeholder="Wrong size"
        required
        error={state?.field.reason}
      />

      {/*
        ── HOW THE MONEY WENT BACK, WITH NO PRESELECTED ANSWER ────────────
        Only a CASH refund comes out of a drawer, and that is the one the
        shift's expected cash is adjusted for. A default of cash would be
        right most of the time and wrong silently: an M-Pesa reversal filed
        as cash reads as a shortfall at a close days later, with nothing
        left to connect it to. So the first option is not a choice.
      */}
      <Select
        name="method"
        label="How the money went back"
        hint="Only cash comes out of a drawer, and only cash changes what the
              till is expected to hold at the close."
        defaultValue=""
        required
        error={state?.field.method}
      >
        <option value="" disabled>
          Choose…
        </option>
        <option value="cash">Cash, out of the drawer</option>
        <option value="mpesa">M-Pesa</option>
        <option value="card">Card</option>
        <option value="bank">Bank transfer</option>
        {/*
          Offered only with a customer on the sale. The server refuses a
          credit refund without one, because there would be no balance to
          reduce and the money would have been written off instead.
        */}
        {sale.customer != null ? (
          <option value="credit">On account</option>
        ) : null}
      </Select>

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
