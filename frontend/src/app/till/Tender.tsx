"use client";

import { cents, shillings } from "./money";
import styles from "./till.module.css";

/**
 * Taking cash.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE NOTES ARE THE INTERFACE, BECAUSE THE NOTES ARE WHAT IS IN THE HAND.
 *
 * A customer does not say "two hundred and fifty"; they put a 200 and a 50 on
 * the counter. Typing "250" makes the cashier translate that into digits with
 * a queue watching, and the mistake it invites — a stray zero — is a wrong
 * change figure read out loud before anybody notices.
 *
 * So the denominations ADD UP as they are tapped. Two 500s is one tap, twice,
 * and the running tendered figure is the pile on the counter. That is also
 * why there are no coins here: nobody tenders a shop's change back in 5s, and
 * six more buttons would push the total off a short screen.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ⚠ NOTHING HERE DECIDES WHAT IS CHARGED. The tendered amount only produces
 *   the change figure; the sale is priced by the server and the payment
 *   recorded is the total, not what was handed over. Sending the tender would
 *   post a cash overage as revenue — see the note in Till.tsx.
 */

/**
 * Kenyan notes, largest last so the common ones sit under the thumb.
 *
 * 1000 and 500 do most of the work; 50 is the smallest note anybody tenders
 * with. A shop wanting different denominations is a setting for the day there
 * is a second currency, not a guess to make now.
 */
const NOTES = [50, 100, 200, 500, 1000];

export function Tender({
  totalCents,
  tendered,
  onChange,
}: {
  totalCents: number;
  tendered: string;
  onChange: (next: string) => void;
}) {
  const given = cents(tendered || "0");
  const change = given - totalCents;
  const started = tendered.trim() !== "";

  function add(note: number) {
    // Accumulates, because cash arrives in pieces. Kept as a plain decimal
    // string so the field stays the one somebody can also type into.
    onChange(((given + note * 100) / 100).toFixed(2));
  }

  return (
    <div className={styles.tender}>
      <div className={styles.notes}>
        {NOTES.map((note) => (
          <button
            key={note}
            type="button"
            className={styles.note}
            onClick={() => add(note)}
          >
            +{note}
          </button>
        ))}
        <button
          type="button"
          className={`${styles.note} ${styles.noteExact}`}
          onClick={() => onChange((totalCents / 100).toFixed(2))}
          disabled={totalCents <= 0}
        >
          Exact
        </button>
      </div>

      <div className={styles.tenderRow}>
        <label className={styles.tenderLabel} htmlFor="tendered">
          Cash given
        </label>
        <input
          id="tendered"
          className={`${styles.tenderInput} ${styles.mono}`}
          inputMode="decimal"
          placeholder="0.00"
          value={tendered}
          onChange={(event) => onChange(event.target.value)}
        />
        {started ? (
          <button
            type="button"
            className={styles.tenderClear}
            onClick={() => onChange("")}
            aria-label="Clear the amount given"
          >
            Clear
          </button>
        ) : null}
      </div>

      {/*
        The change is the number read out loud, so it is the largest thing
        here — and "short by" is a different colour from change because
        handing back money you have not been given is the error this line
        exists to prevent.
      */}
      <div
        className={`${styles.changeLine} ${
          !started ? "" : change >= 0 ? styles.changeDue : styles.changeShort
        }`}
        // Announced when it changes: a cashier looking at the customer still
        // hears their screen reader say the figure.
        aria-live="polite"
      >
        {!started ? (
          <span className={styles.changeIdle}>
            Tap what they hand over, or type it
          </span>
        ) : change >= 0 ? (
          <>
            <span className={styles.changeWord}>Change</span>
            <span className={styles.changeValue}>{shillings(change)}</span>
          </>
        ) : (
          <>
            <span className={styles.changeWord}>Short by</span>
            <span className={styles.changeValue}>{shillings(-change)}</span>
          </>
        )}
      </div>
    </div>
  );
}
