"use client";

import { useActionState, useState } from "react";

import { General, Submit, Text } from "@/components/Form";
import { closeTill, type Drawer, type State } from "./actions";
import { ksh } from "@/lib/money";
import styles from "./reports.module.css";

const NONE: State = null;

/**
 * Everything that moved the expected figure, in the order it moved it.
 *
 * ── SIGNED, AND EVERY TERM OR THE LIST DOES NOT ADD UP ────────────────────
 *
 * A reader checks a breakdown by adding it. `paid_in` is an ADDITION — a
 * float fetched from the safe — and a list of deductions that quietly left
 * it out would stop short of "Should have held" by exactly that amount, on
 * the screen whose whole job is explaining where a figure came from.
 *
 * So each row carries its own sign and the caller prints it. Nil rows are
 * dropped: a close with no safe drop and no returns should read as two lines,
 * not five.
 */
function terms(drawer: Drawer): { label: string; sign: "+" | "−"; value: string }[] {
  const rows: { label: string; sign: "+" | "−"; value: string }[] = [
    { label: "Change given", sign: "−", value: drawer.change_given },
    { label: "Cash brought in", sign: "+", value: drawer.paid_in },
    { label: "Cash taken out", sign: "−", value: drawer.paid_out },
    { label: "Returns paid out", sign: "−", value: drawer.refunded_cash },
  ];
  return rows.filter((row) => Number(row.value) !== 0);
}

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
        {/*
          ── THE BREAKDOWN IS SHOWN BECAUSE THIS IS WHERE IT IS ASKED FOR ───
          A manager reading a shortfall asks where it came from, and the
          answer used to require another screen. The rows that are nil are
          omitted rather than printed as 0.00: a close with no returns and no
          safe drop should read as three lines, not six.
        */}
        <dl className={styles.closedFigures}>
          <div>
            <dt>Opened with</dt>
            <dd>{ksh(state.drawer.opening_cash)}</dd>
          </div>
          <div>
            <dt>Taken</dt>
            <dd>{ksh(state.drawer.cash_taken)}</dd>
          </div>
          {terms(state.drawer).map((term) => (
            <div key={term.label}>
              <dt>{term.label}</dt>
              <dd>
                {term.sign}
                {ksh(term.value)}
              </dd>
            </div>
          ))}
          <div className={styles.closedRule}>
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
