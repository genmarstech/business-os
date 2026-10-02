"use client";

import { useActionState, useState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import { amount } from "@/lib/money";
import {
  abandonCount,
  closeCount,
  openCount,
  recordLine,
  type State,
} from "./actions";
import type { Branch, Count, Stock } from "./types";
import styles from "./counts.module.css";

const NONE: State = null;

export function OpenCountForm({ branches }: { branches: Branch[] }) {
  const [state, action] = useActionState(openCount, NONE);

  return (
    <form action={action} className={styles.form}>
      <General messages={state?.general ?? []} />

      <Row>
        <Select
          name="branch"
          label="Branch"
          required
          error={state?.field.branch}
        >
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>
        <Text
          name="note"
          label="What is being counted"
          hint="Optional. “The back room”, “spirits only”."
          placeholder="The whole shop"
          error={state?.field.note}
        />
      </Row>

      <Submit pending="Opening…">Start counting</Submit>
    </form>
  );
}

/**
 * The sheet for an open count: what has been counted, and what has not.
 *
 * ── UNCOUNTED SHELVES ARE LISTED, AND THEY ARE NOT A TO-DO LIST ───────────
 * A partial count is the ordinary case — one aisle on a Tuesday — so the
 * uncounted rows sit below a heading that says so, rather than above a
 * progress bar implying the count is unfinished until the catalogue runs
 * out. `count_summary` makes the same decision on the server: it counts
 * lines entered, never products stocked.
 */
export function CountSheet({
  count,
  stock,
  canCount,
  canClose,
}: {
  count: Count;
  stock: Stock[];
  canCount: boolean;
  canClose: boolean;
}) {
  const counted = new Set(count.lines.map((line) => line.inventory));
  const remaining = stock.filter((row) => !counted.has(row.id));

  return (
    <div className={styles.sheet}>
      {count.lines.length > 0 ? (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Product</th>
                <th className={styles.num}>System</th>
                <th className={styles.num}>Counted</th>
                <th className={styles.num}>Difference</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {count.lines.map((line) => {
                const variance = Number(line.variance);
                return (
                  <tr key={line.id}>
                    <td>
                      <div className={styles.name}>{line.product_name}</div>
                      <div className={styles.meta}>
                        {line.product_sku}
                        {line.counted_by_name
                          ? ` · ${line.counted_by_name}`
                          : ""}
                      </div>
                    </td>
                    <td className={styles.num}>
                      {amount(line.expected_quantity)}
                    </td>
                    <td className={styles.num}>
                      {amount(line.counted_quantity)}
                    </td>
                    <td
                      className={`${styles.num} ${
                        variance < 0
                          ? styles.bad
                          : variance > 0
                            ? styles.odd
                            : ""
                      }`}
                    >
                      {variance > 0 ? "+" : ""}
                      {amount(line.variance)}
                    </td>
                    <td className={styles.right}>
                      {canCount ? (
                        <RecordForm
                          countId={count.id}
                          inventoryId={line.inventory}
                          productName={line.product_name}
                          label="Recount"
                        />
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.empty}>
          Nothing counted yet. Pick a shelf below and write down what is on
          it.
        </p>
      )}

      {canCount && remaining.length > 0 ? (
        <details className={styles.more}>
          <summary className={styles.summary}>
            {remaining.length}{" "}
            {remaining.length === 1 ? "shelf" : "shelves"} not counted
          </summary>
          <p className={styles.panelLede}>
            Counting part of a branch is normal. Only what is listed above
            will be booked when this count closes — the rest is left exactly
            as it is.
          </p>
          <ul className={styles.shelves}>
            {remaining.map((row) => (
              <li key={row.id}>
                <span className={styles.name}>{row.product_name}</span>
                <span className={styles.meta}>{row.product_sku}</span>
                <RecordForm
                  countId={count.id}
                  inventoryId={row.id}
                  productName={row.product_name}
                  label="Count it"
                />
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {canClose ? (
        <CloseActions count={count} />
      ) : (
        /*
          Disabled with a reason rather than hidden — the Shell's rule. The
          person reading this took the count; what they need to know is who
          signs it off, not that signing off does not exist.
        */
        <p className={styles.waiting}>
          Waiting on somebody who can sign off what this found. The owner, or
          a branch manager.
        </p>
      )}
    </div>
  );
}

function RecordForm({
  countId,
  inventoryId,
  productName,
  label,
}: {
  countId: number;
  inventoryId: number;
  productName: string;
  label: string;
}) {
  const [state, action] = useActionState(recordLine, NONE);
  const [open, setOpen] = useState(false);

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        {label}
      </button>
    );
  }

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="count_id" value={countId} />
      <input type="hidden" name="inventory" value={inventoryId} />
      <General messages={state?.general ?? []} />

      <p className={styles.formLede}>
        {productName} — how many are actually on the shelf. Counting it again
        replaces the figure; the last one entered is the one that counts.
      </p>

      <Text
        name="counted"
        label="On the shelf"
        inputMode="decimal"
        mono
        required
        autoFocus
        error={state?.field.counted}
      />
      <Text
        name="note"
        label="Note"
        hint="Optional. Two crates behind the door, one box crushed."
        error={state?.field.note}
      />

      <Submit pending="Saving…">Write it down</Submit>
    </form>
  );
}

/**
 * Closing, and the other way out of a count.
 *
 * Both are behind a confirmation, and they confirm different things. Closing
 * books every variance and cannot be undone. Abandoning books nothing and
 * demands a reason, because an abandoned count is the one somebody will ask
 * about later and "no reason given" is the answer that makes them ask again.
 */
function CloseActions({ count }: { count: Count }) {
  const [closed, close] = useActionState(closeCount, NONE);
  const [abandoned, abandon] = useActionState(abandonCount, NONE);
  const [confirming, setConfirming] = useState(false);
  const [leaving, setLeaving] = useState(false);

  const s = count.summary;
  const moves = s.short + s.over;

  return (
    <div className={styles.actions}>
      <General
        messages={[...(closed?.general ?? []), ...(abandoned?.general ?? [])]}
      />

      {confirming ? (
        <form action={close} className={styles.confirm}>
          <input type="hidden" name="count_id" value={count.id} />
          <p>
            {moves === 0
              ? "Every shelf counted agreed with the system. Closing books nothing and records that the count was taken and found nothing wrong — which is a result worth having written down."
              : `This books ${moves} adjustment${
                  moves === 1 ? "" : "s"
                } against the stock, each one a movement you can trace back to this count. It cannot be undone — a miscount is corrected by counting again.`}
          </p>
          <Submit pending="Booking…">Close and book it</Submit>
          <button
            type="button"
            className={styles.ghost}
            onClick={() => setConfirming(false)}
          >
            Not yet
          </button>
        </form>
      ) : (
        <button
          type="button"
          className={styles.primary}
          onClick={() => setConfirming(true)}
        >
          Close the count
        </button>
      )}

      {leaving ? (
        <form action={abandon} className={styles.confirm}>
          <input type="hidden" name="count_id" value={count.id} />
          <Text
            name="reason"
            label="Why is this being abandoned?"
            placeholder="Counted the wrong aisle"
            required
            error={abandoned?.field.reason}
          />
          <Submit pending="Abandoning…">Abandon it</Submit>
          <button
            type="button"
            className={styles.ghost}
            onClick={() => setLeaving(false)}
          >
            Keep counting
          </button>
        </form>
      ) : (
        <button className={styles.quiet} onClick={() => setLeaving(true)}>
          Abandon this count
        </button>
      )}
    </div>
  );
}
