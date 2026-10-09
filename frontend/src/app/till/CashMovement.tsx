"use client";

import { useState } from "react";

import { call, readError } from "./session";
import styles from "./till.module.css";

/**
 * Recording cash in or out of the drawer, mid-shift.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE DRAWER COUNT WAS WRONG ON ANY DAY SOMEBODY BANKED THE TAKINGS.
 *
 * Expected cash was opening float plus cash taken less change given. Lift
 * 5,000 out at lunchtime to walk it to the bank, count the drawer at six, and
 * the till says it is 5,000 short — for the most ordinary thing a cash
 * business does. Since notifications shipped it also pages every holder of
 * `reports.branch` about money nobody lost.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── TAKING CASH OUT NEEDS A MANAGER, AND THE SCREEN SAYS SO FIRST ─────────
 *
 * A pay-in only ever makes the drawer look MORE short, so a cashier records
 * their own. A pay-out lowers the expected figure — a cashier who could
 * record one could take money and write the shortfall away in the same
 * movement, which is what a drawer count exists to catch. So the server
 * refuses it without `shift.close`.
 *
 * The refusal is explained here BEFORE the button is pressed rather than
 * after, because "ask your manager" is something somebody can act on and a
 * 403 at a counter is not. The option stays visible rather than hidden: the
 * cashier needs to know the manager can do it, which is the whole next step.
 */

type Kind = "float_in" | "safe_drop" | "pay_out";

const KINDS: { value: Kind; label: string; hint: string; outward: boolean }[] = [
  {
    value: "float_in",
    label: "Change brought in",
    hint: "More notes or coins into the drawer, usually from the safe.",
    outward: false,
  },
  {
    value: "safe_drop",
    label: "Dropped to the safe or banked",
    hint: "Taken out for safekeeping. The drawer should hold less after this.",
    outward: true,
  },
  {
    value: "pay_out",
    label: "Paid out of the drawer",
    hint: "A supplier at the door, a refund in cash, petty cash.",
    outward: true,
  },
];

export function CashMovement({
  shiftId,
  maySendOut,
  onRecorded,
  onClose,
}: {
  shiftId: number;
  /**
   * Whether this principal holds `shift.close`.
   *
   * Drawn from the permission list, and NOT security — the server refuses a
   * pay-out from anybody without it regardless of what this says. All it does
   * is let the screen explain in advance instead of after a refusal.
   */
  maySendOut: boolean;
  onRecorded: (expected: string) => void;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<Kind>("float_in");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const chosen = KINDS.find((k) => k.value === kind)!;
  const blocked = chosen.outward && !maySendOut;

  async function save() {
    setError("");
    setDone("");

    const value = Number(amount);
    if (!Number.isFinite(value) || value <= 0) {
      setError("How much moved?");
      return;
    }
    if (!reason.trim()) {
      setError("Say why it moved. That is what this is for.");
      return;
    }

    setBusy(true);
    try {
      const answer = await call<{ drawer?: { expected_cash?: string } }>(
        `/brn/register-shifts/${shiftId}/cash/`,
        {
          method: "POST",
          body: { kind, amount, reason: reason.trim() },
        },
      );
      const expected = answer?.drawer?.expected_cash ?? "";
      setDone(
        expected
          ? `Recorded. The drawer should now hold ${expected}.`
          : "Recorded.",
      );
      setAmount("");
      setReason("");
      if (expected) onRecorded(expected);
    } catch (caught) {
      setError(readError(caught, "That did not save. Try again."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.tool} role="dialog" aria-label="Cash in or out">
      <h2 className={styles.toolTitle}>Cash in or out</h2>
      <p className={styles.toolLede}>
        Anything that moves in or out of this drawer other than a sale. Record
        it now and the count at the end will add up.
      </p>

      {error ? (
        <p className={styles.gateError} role="alert">
          {error}
        </p>
      ) : null}
      {done ? (
        <p className={styles.toolDone} role="status">
          {done}
        </p>
      ) : null}

      <label className={styles.gateLabel} htmlFor="cash-kind">
        What happened
      </label>
      <select
        id="cash-kind"
        className={styles.gateInput}
        value={kind}
        onChange={(e) => setKind(e.target.value as Kind)}
      >
        {KINDS.map((k) => (
          <option key={k.value} value={k.value}>
            {k.label}
          </option>
        ))}
      </select>
      <span className={styles.gateHint}>{chosen.hint}</span>

      {blocked ? (
        <p className={styles.toolNote}>
          Taking cash out needs a manager — somebody who can close a till. Ask
          them to record it, or the count at the end will read short by this
          much and nobody will know why.
        </p>
      ) : null}

      <label className={styles.gateLabel} htmlFor="cash-amount">
        How much
      </label>
      <input
        id="cash-amount"
        className={`${styles.gateInput} ${styles.mono}`}
        type="number"
        min="0"
        step="0.01"
        inputMode="decimal"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        disabled={blocked}
      />

      <label className={styles.gateLabel} htmlFor="cash-reason">
        Why
      </label>
      <input
        id="cash-reason"
        className={styles.gateInput}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Banked at lunchtime"
        disabled={blocked}
      />
      <span className={styles.gateHint}>
        Somebody will read this when the drawer does not match. Write what you
        would say out loud.
      </span>

      <button
        type="button"
        className={styles.gateButton}
        disabled={busy || blocked}
        onClick={() => void save()}
      >
        {busy ? "Recording…" : "Record it"}
      </button>
      <button type="button" className={styles.gateQuiet} onClick={onClose}>
        Close
      </button>
    </div>
  );
}
