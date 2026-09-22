"use client";

import { useActionState, useState } from "react";

import { General, Submit, Text } from "@/components/Form";
import { closeTill, type State } from "./actions";
import { ksh } from "@/lib/money";
import styles from "./reports.module.css";

const NONE: State = null;

/**
 * Count the drawer, then close the till.
 *
 * ── THE EXPECTED FIGURE IS SHOWN, AND THAT IS A DELIBERATE RISK ────────────
 *
 * Telling somebody what the answer should be before they count invites them
 * to type it. The alternative — count blind, then reveal — is what an audit
 * would want, and it is not what a shop at closing time will tolerate: the
 * figure is already on the reports screen above this form, so hiding it here
 * would only be theatre.
 *
 * What makes the count meaningful is instead that the counter is not the
 * cashier: SHIFT_CLOSE is a manager's permission, and a cashier who tries is
 * refused by the server, not by this component.
 */
export function CloseTill({
  shiftId,
  registerName,
  expected,
}: {
  shiftId: number;
  registerName: string;
  expected: string;
}) {
  const [state, action] = useActionState(closeTill, NONE);
  const [open, setOpen] = useState(false);

  if (state?.drawer) {
    const variance = Number(state.drawer.variance ?? "0");
    const over = variance > 0;
    const exact = variance === 0;
    return (
      <div className={styles.closed}>
        <div className={styles.closedTitle}>
          {registerName} closed
        </div>
        <dl className={styles.closedFigures}>
          <div>
            <dt>Should have held</dt>
            <dd>{ksh(state.drawer.expected_cash)}</dd>
          </div>
          <div>
            <dt>Counted</dt>
            <dd>{ksh(state.drawer.counted_cash)}</dd>
          </div>
          <div>
            <dt>{exact ? "Balanced" : over ? "Over by" : "Short by"}</dt>
            <dd
              className={
                exact ? styles.exact : over ? styles.over : styles.short
              }
            >
              {exact ? "—" : ksh(String(Math.abs(variance)))}
            </dd>
          </div>
        </dl>
        {exact ? null : (
          <p className={styles.closedNote}>
            Recorded against this shift. It cannot be closed again — a second
            count would only agree with itself.
          </p>
        )}
      </div>
    );
  }

  if (!open) {
    return (
      <button className={styles.quiet} onClick={() => setOpen(true)}>
        Close and count
      </button>
    );
  }

  return (
    <form action={action} className={styles.closeForm}>
      <input type="hidden" name="shift_id" value={shiftId} />
      <General messages={state?.general ?? []} />

      <p className={styles.closeLede}>
        Count {registerName}. It should hold{" "}
        <strong>{ksh(expected)}</strong>, including the float it opened with.
      </p>

      <Text
        name="counted_cash"
        label="What is actually in the drawer"
        inputMode="decimal"
        mono
        required
        autoFocus
        error={state?.field.counted_cash}
      />

      <Submit pending="Closing…">Close the till</Submit>
    </form>
  );
}
