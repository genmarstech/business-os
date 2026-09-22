"use client";

import { useState } from "react";

import { call, readError } from "./session";
import styles from "./till.module.css";

/**
 * What the person on the till wants the manager to know.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * IT IS WRITTEN BEFORE ANYBODY COUNTS, AND THE SERVER FREEZES IT AT THE CLOSE.
 *
 * A drawer twenty short with "took 20 for a customer who had no change, chit
 * in the drawer" is an ordinary evening. The same drawer twenty short in
 * silence is a conversation, and possibly a dismissal.
 *
 * The note is only worth that difference if it was written before its author
 * knew the number — so the endpoint refuses once the shift is closed. Nobody
 * gets to explain a variance after seeing it.
 * ══════════════════════════════════════════════════════════════════════════
 */
export function ShiftNote({
  shiftId,
  note,
  onSaved,
  onClose,
}: {
  shiftId: number;
  note: string;
  onSaved: (next: string) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(note);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  async function save() {
    setBusy(true);
    setError("");
    try {
      await call(`/brn/register-shifts/${shiftId}/note/`, {
        method: "POST",
        body: { note: draft },
      });
      onSaved(draft);
      setSaved(true);
    } catch (caught) {
      setError(readError(caught, "That did not save."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.tool} role="dialog" aria-label="Note for the manager">
      <div className={styles.toolHead}>
        <span className={styles.toolTitle}>Note for your manager</span>
        <button className={styles.toolClose} onClick={onClose}>
          Close
        </button>
      </div>

      <p className={styles.toolLede}>
        Anything about this shift they should know before they count the
        drawer — a float taken for change, a jam, a customer coming back for a
        refund. It is read beside the count, so write it as it happens.
      </p>

      {error ? (
        <p className={styles.toolError} role="alert">
          {error}
        </p>
      ) : null}

      <textarea
        className={styles.noteBox}
        rows={5}
        value={draft}
        placeholder="Took 200 for a customer who had no change — chit in the drawer."
        onChange={(event) => {
          setDraft(event.target.value);
          setSaved(false);
        }}
      />

      <div className={styles.toolActions}>
        <span className={styles.toolHint}>
          {saved ? "Saved." : draft === note ? "" : "Not saved yet."}
        </span>
        <button
          className={styles.toolSave}
          onClick={() => void save()}
          disabled={busy || draft === note}
        >
          {busy ? "Saving…" : "Save note"}
        </button>
      </div>
    </div>
  );
}
