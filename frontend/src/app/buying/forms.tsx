"use client";

import { useActionState, useState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import { amount, ksh } from "@/lib/money";
import {
  addSupplier,
  approveOrder,
  cancelOrder,
  raiseOrder,
  receiveDelivery,
  sendOrder,
  setSupplierActive,
  type State,
} from "./actions";
import type { Branch, Order, Product, Supplier } from "./types";
import styles from "./buying.module.css";

const NONE: State = null;

/**
 * One line of a new order.
 *
 * Held in client state rather than posted one at a time, because an order
 * without its lines is not an order — the endpoint takes the whole document
 * and writes it in one transaction.
 */
type Draft = { key: number; product: string; quantity: string; cost: string };

let nextKey = 1;
const blank = (): Draft => ({
  key: nextKey++,
  product: "",
  quantity: "",
  cost: "",
});

export function RaiseOrderForm({
  branches,
  suppliers,
  products,
}: {
  branches: Branch[];
  suppliers: Supplier[];
  products: Product[];
}) {
  const [state, action] = useActionState(raiseOrder, NONE);
  const [lines, setLines] = useState<Draft[]>([blank()]);

  const change = (key: number, field: keyof Draft, value: string) =>
    setLines((rows) =>
      rows.map((row) => (row.key === key ? { ...row, [field]: value } : row)),
    );

  const byId = new Map(products.map((p) => [String(p.id), p]));

  return (
    <form action={action} className={styles.form}>
      <General messages={state?.general ?? []} />

      <Row>
        <Select name="branch" label="Deliver to" required error={state?.field.branch}>
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>
        <Select
          name="supplier"
          label="Supplier"
          required
          error={state?.field.supplier}
        >
          {suppliers.map((supplier) => (
            <option key={supplier.id} value={supplier.id}>
              {supplier.name}
            </option>
          ))}
        </Select>
      </Row>

      <Row>
        <Text
          name="expected_at"
          label="Expected"
          type="date"
          hint="When you think it will arrive. A note to yourselves, not a promise."
          error={state?.field.expected_at}
        />
        <Text
          name="note"
          label="Note"
          placeholder="Quoted by phone, confirm before sending"
          error={state?.field.note}
        />
      </Row>

      <div className={styles.lines}>
        <div className={styles.linesHead}>What to order</div>

        {lines.map((line) => {
          const product = byId.get(line.product);
          return (
            <div key={line.key} className={styles.line}>
              <select
                name="product"
                aria-label="Product"
                className={styles.lineSelect}
                value={line.product}
                onChange={(event) =>
                  change(line.key, "product", event.target.value)
                }
              >
                <option value="">Choose a product…</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} — {p.sku}
                  </option>
                ))}
              </select>

              <input
                name="quantity"
                aria-label="How many"
                inputMode="decimal"
                placeholder="Qty"
                className={styles.lineNumber}
                value={line.quantity}
                onChange={(event) =>
                  change(line.key, "quantity", event.target.value)
                }
              />

              <input
                name="unit_cost"
                aria-label="Cost each"
                inputMode="decimal"
                /*
                  Blank means "whatever the catalogue says", which the server
                  fills in. Showing the catalogue cost as the placeholder says
                  what blank will do without pretending somebody typed it.
                */
                placeholder={product ? ksh(product.cost_price) : "Cost each"}
                className={styles.lineNumber}
                value={line.cost}
                onChange={(event) => change(line.key, "cost", event.target.value)}
              />

              <button
                type="button"
                className={styles.lineDrop}
                aria-label="Remove this line"
                onClick={() =>
                  setLines((rows) =>
                    rows.length === 1
                      ? [blank()]
                      : rows.filter((row) => row.key !== line.key),
                  )
                }
              >
                ×
              </button>
            </div>
          );
        })}

        <button
          type="button"
          className={styles.quiet}
          onClick={() => setLines((rows) => [...rows, blank()])}
        >
          Add another product
        </button>
      </div>

      {state?.field["items[0].product"] ? (
        <p className={styles.lineError} role="alert">
          {state.field["items[0].product"]}
        </p>
      ) : null}

      <Submit pending="Raising…">Raise the order</Submit>
    </form>
  );
}

/**
 * The buttons that move an order along.
 *
 * Each is its own form rather than one form with several submits: they go to
 * different endpoints, and a shared one would need the button to carry the
 * destination — which is the kind of thing that ends up pointing at `approve`
 * from a button labelled `send`.
 */
export function OrderActions({
  order,
  canSend,
  canDecide,
  canReceive,
}: {
  order: Order;
  canSend: boolean;
  canDecide: boolean;
  canReceive: boolean;
}) {
  const [sent, send] = useActionState(sendOrder, NONE);
  const [approved, approve] = useActionState(approveOrder, NONE);
  const [cancelled, cancel] = useActionState(cancelOrder, NONE);

  const messages = [
    ...(sent?.general ?? []),
    ...(approved?.general ?? []),
    ...(cancelled?.general ?? []),
  ];

  const open = order.status !== "received" && order.status !== "cancelled";

  return (
    <div className={styles.actions}>
      <General messages={messages} />

      {order.status === "draft" && canSend ? (
        <form action={send}>
          <input type="hidden" name="order_id" value={order.id} />
          <Submit pending="Sending…">Send to the supplier</Submit>
        </form>
      ) : null}

      {order.status === "submitted" ? (
        canDecide ? (
          <form action={approve}>
            <input type="hidden" name="order_id" value={order.id} />
            <Submit pending="Approving…">Approve</Submit>
          </form>
        ) : (
          /*
            Disabled with a reason rather than hidden — the Shell's rule. The
            person looking at this raised the order; what they need to know is
            who signs it off, not that approval does not exist.
          */
          <p className={styles.waiting}>
            Waiting on somebody who can approve it. A manager or the owner.
          </p>
        )
      ) : null}

      {open && canDecide ? <CancelForm orderId={order.id} action={cancel} /> : null}

      {canReceive && (order.status === "approved" || order.status === "part_received") ? (
        <ReceiveForm order={order} />
      ) : null}
    </div>
  );
}

function CancelForm({
  orderId,
  action,
}: {
  orderId: number;
  action: (formData: FormData) => void;
}) {
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        Cancel this order
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="order_id" value={orderId} />
      <Text
        name="reason"
        label="Why"
        hint="Anything already delivered stays delivered. This says nothing further is coming."
        placeholder="Supplier out of stock"
        autoFocus
      />
      <Submit pending="Cancelling…">Cancel it</Submit>
    </form>
  );
}

/**
 * Count the delivery in.
 *
 * ── A KEY PER ATTEMPT, MINTED WHEN THE FORM OPENS ──────────────────────────
 * `crypto.randomUUID()` in a `useState` initialiser runs only in the browser
 * here, because this form does not exist until somebody clicks — so there is
 * nothing for the server to render differently and no hydration mismatch.
 * Open it again and a new key is minted, which is correct: that is a second
 * delivery, not a retry of the first.
 */
export function ReceiveForm({ order }: { order: Order }) {
  const [state, action] = useActionState(receiveDelivery, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.primary} onClick={() => setOpen(true)}>
        Book in a delivery
      </button>
    );
  }

  return <ReceiveFields order={order} action={action} state={state} />;
}

function ReceiveFields({
  order,
  action,
  state,
}: {
  order: Order;
  action: (formData: FormData) => void;
  state: State;
}) {
  const [key] = useState(() => crypto.randomUUID());
  const outstanding = order.items.filter((line) => Number(line.outstanding) > 0);

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="order_id" value={order.id} />
      <input type="hidden" name="idempotency_key" value={key} />
      <General messages={state?.general ?? []} />

      <p className={styles.formLede}>
        Count what is on the lorry, not what the paperwork says. Leave a line
        blank if none of it came — the rest of the order stays open.
      </p>

      <div className={styles.lines}>
        {outstanding.map((line) => (
          <div key={line.id} className={styles.receiveLine}>
            <input type="hidden" name="item" value={line.id} />
            <div>
              <div className={styles.name}>{line.product_name}</div>
              <div className={styles.meta}>
                {amount(line.outstanding)} still to come
              </div>
            </div>
            <input
              name="received"
              aria-label={`How many ${line.product_name} arrived`}
              inputMode="decimal"
              placeholder="0"
              className={styles.lineNumber}
            />
          </div>
        ))}
      </div>

      {state?.field.quantity ? (
        <p className={styles.lineError} role="alert">
          {state.field.quantity}
        </p>
      ) : null}

      <Row>
        <Text
          name="delivery_note"
          label="Their delivery note"
          hint="The reference on the paper that came with it."
          placeholder="DN-5512"
          error={state?.field.delivery_note}
        />
        <Text name="note" label="Note" placeholder="One crate damaged, refused" />
      </Row>

      <Submit pending="Booking in…">Book it in</Submit>
    </form>
  );
}

export function AddSupplierForm({ organisationId }: { organisationId: number }) {
  const [state, action] = useActionState(addSupplier, NONE);

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organisation" value={organisationId} />
      <General messages={state?.general ?? []} />

      <Row>
        <Text name="name" label="Name" required error={state?.field.name} />
        <Text
          name="contact_person"
          label="Who you deal with"
          error={state?.field.contact_person}
        />
      </Row>

      <Row>
        <Text
          name="phone_number"
          label="Phone"
          inputMode="tel"
          error={state?.field.phone_number}
        />
        <Text name="email" label="Email" type="email" error={state?.field.email} />
      </Row>

      <Row>
        <Text name="address" label="Address" error={state?.field.address} />
        <Text
          name="lead_time_days"
          label="Usually takes (days)"
          inputMode="numeric"
          mono
          defaultValue="0"
          error={state?.field.lead_time_days}
        />
      </Row>

      <Text name="note" label="Note" error={state?.field.note} />

      <Submit pending="Adding…">Add the supplier</Submit>
    </form>
  );
}

export function ArchiveSupplier({
  supplierId,
  active,
}: {
  supplierId: number;
  active: boolean;
}) {
  const [state, action] = useActionState(setSupplierActive, NONE);

  return (
    <form action={action}>
      <input type="hidden" name="supplier_id" value={supplierId} />
      <input type="hidden" name="active" value={active ? "no" : "yes"} />
      <General messages={state?.general ?? []} />
      <button className={styles.quiet} type="submit">
        {active ? "Archive" : "Restore"}
      </button>
    </form>
  );
}
