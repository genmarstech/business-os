"use client";

import { useActionState, useEffect, useState } from "react";

import { General, Submit, Text } from "@/components/Form";
import { ksh } from "@/lib/money";
import type { SafePayload, SafePendingDrop } from "@/lib/reports";
import { bankSafe, receiveDrop, type SafeState } from "./actions";
import styles from "./reports.module.css";

const NONE: SafeState = null;

/**
 * A branch's safe: what it holds, what is waiting to be received into it,
 * and what banking it has required.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THIS IS THE SCREEN "BANKING A DAY'S TAKINGS ACROSS TILLS" HAD NO SCREEN FOR.
 *
 * `CashMovement.Kind.SAFE_DROP` only ever lowered a till's own drawer — the
 * README said so plainly: "banking a day's takings across tills, and a safe
 * of its own, are not modelled." A manager carrying cash off three registers
 * had nowhere to confirm it arrived, and nothing recorded what was banked
 * against the figure to bank it against. `Safe` and `SafeMovement` on the
 * server are that other end; this is where a manager uses them.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Rendered only where `canManage` — the page checks `shift.close`, the same
 * permission the server re-checks by hand against THIS branch, because a
 * manager somewhere else in the organisation does not get to bank a safe
 * they do not run.
 */
export function Safe({
  branchId,
  branchName,
  initial,
}: {
  branchId: number;
  branchName: string;
  initial: SafePayload;
}) {
  const [payload, setPayload] = useState(initial);

  return (
    <section className={styles.panel}>
      <h2 className={styles.panelTitle}>{branchName}&rsquo;s safe</h2>
      <p className={styles.panelLede}>
        What a till drops here is confirmed on arrival below, and what the
        safe holds is banked against a deposit slip — the step this product
        had nowhere to record until now.
      </p>

      <dl className={styles.tally}>
        <div>
          <dt>Holding now</dt>
          <dd className={styles.strong}>{ksh(payload.safe.balance)}</dd>
        </div>
      </dl>

      {payload.pending_drops.length > 0 ? (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Till</th>
                <th>Why it left the drawer</th>
                <th className={styles.num}>Amount</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {payload.pending_drops.map((drop) => (
                <ReceiveRow
                  key={drop.id}
                  branchId={branchId}
                  drop={drop}
                  onReceived={setPayload}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={styles.empty}>
          Nothing has left a till for this safe and not yet been confirmed.
        </p>
      )}

      <BankForm
        branchId={branchId}
        balance={payload.safe.balance}
        onBanked={setPayload}
      />

      {payload.movements.length > 0 ? (
        <div className={styles.scroll}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Movement</th>
                <th>Reference</th>
                <th className={styles.num}>Amount</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {payload.movements.map((movement) => (
                <tr key={movement.id}>
                  <td>
                    {movement.kind_label}
                    {movement.note ? (
                      <div className={styles.meta}>{movement.note}</div>
                    ) : null}
                  </td>
                  <td>{movement.reference || "—"}</td>
                  <td className={styles.num}>
                    {movement.kind === "banked" ? "−" : "+"}
                    {ksh(movement.amount)}
                  </td>
                  <td className={styles.meta}>{when(movement.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/**
 * One drop, waiting to be confirmed. Its own form, not a row in a shared one
 * — each drop is received independently and a shared submit would confirm
 * every pending drop at once on a single click.
 */
function ReceiveRow({
  branchId,
  drop,
  onReceived,
}: {
  branchId: number;
  drop: SafePendingDrop;
  onReceived: (payload: SafePayload) => void;
}) {
  const [state, action] = useActionState(receiveDrop, NONE);

  useEffect(() => {
    if (state?.payload) onReceived(state.payload);
    // `onReceived` is the parent's setState, stable across renders, and
    // including it would re-run this whenever the parent re-renders — which
    // is every time THIS effect calls it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return (
    <tr>
      <td>
        <div className={styles.name}>{drop.register_name}</div>
      </td>
      <td>{drop.reason}</td>
      <td className={styles.num}>{ksh(drop.amount)}</td>
      <td>
        <form action={action}>
          <input type="hidden" name="branch_id" value={branchId} />
          <input type="hidden" name="cash_movement_id" value={drop.id} />
          <Submit pending="Receiving…">Received into safe</Submit>
        </form>
        <General messages={state?.general ?? []} />
      </td>
    </tr>
  );
}

function BankForm({
  branchId,
  balance,
  onBanked,
}: {
  branchId: number;
  balance: string;
  onBanked: (payload: SafePayload) => void;
}) {
  const [state, action] = useActionState(bankSafe, NONE);

  useEffect(() => {
    if (state?.payload) onBanked(state.payload);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  if (Number(balance) <= 0) {
    return null;
  }

  return (
    <form action={action} className={styles.closeForm}>
      <input type="hidden" name="branch_id" value={branchId} />
      <General messages={state?.general ?? []} />

      <p className={styles.closeLede}>
        Bank what the safe holds — up to <strong>{ksh(balance)}</strong>.
      </p>

      <Text
        name="amount"
        label="Amount banked"
        inputMode="decimal"
        mono
        required
        error={state?.field.amount}
      />
      <Text
        name="reference"
        label="Deposit slip or reference number"
        required
        error={state?.field.reference}
      />
      <Text
        name="note"
        label="Note (optional)"
        error={state?.field.note}
      />

      <Submit pending="Recording…">Record as banked</Submit>
    </form>
  );
}

/**
 * Nairobi, the same as every other timestamp on this page — see
 * `closedAt` in page.tsx.
 */
function when(value: string): string {
  return new Date(value).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
