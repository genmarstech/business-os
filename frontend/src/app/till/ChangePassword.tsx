"use client";

import { useState } from "react";
import { call, readError, type TillSession } from "./session";
import styles from "./till.module.css";

/**
 * A cashier changing the password their manager set.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE BACKEND FOR THIS HAS EXISTED ALL ALONG. NOTHING CALLED IT.
 *
 * `POST /auth/staff/password`, `services.change_own_password`, the quality
 * floor, the `must_change_password` flag and the tests around it were all
 * written. The till printed a note saying "ask your manager to show you how
 * to change it" — and there was no how. A manager can only RESET a password,
 * which sets the flag again, so the loop had no exit.
 *
 * The flag is not decoration. StaffCredential's own help text is the claim it
 * makes: "A manager sets the first password, so they know it. Until it is
 * changed, the cashier's actions are not solely attributable to them." Every
 * sale rung up under an unchanged password is a sale two people could have
 * made. That is what this screen is for — attributability, not hygiene.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ── WHY IT DOES NOT HARD-BLOCK THE TILL ─────────────────────────────────────
 *
 * It is the first thing shown after sign-in when the flag is set, before the
 * till is chosen, because that is the one moment a cashier is not mid-queue.
 * But "Do this later" is there, and that is a deliberate choice rather than a
 * softness: a POS that will not open because somebody cannot think of a
 * password at seven in the morning is a shop that cannot sell, and refusing
 * to trade is a worse failure than an attribution gap on one shift.
 *
 * The gate note stays for anyone who defers, so it is asked again next time.
 * Whether to make it mandatory is the business owner's call, not ours — it is
 * one early return away in Till.tsx if they want it.
 */
export function ChangePassword({
  session,
  onChanged,
  onDefer,
}: {
  session: TillSession;
  onChanged: () => void;
  onDefer: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError("");

    // Checked here as well as on the server, because a round trip to be told
    // you typed it differently twice is a round trip at the start of a shift.
    if (next !== again) {
      setError("Those two do not match. Type the new one again.");
      return;
    }

    setBusy(true);
    try {
      await call<void>("/auth/staff/password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      onChanged();
    } catch (caught) {
      // The server's words, not ours. It distinguishes a wrong current
      // password from a weak new one from reusing the old one, and a single
      // "that did not work" would throw all three away.
      setError(readError(caught, "The password would not change."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.gate}>
      <div className={styles.gateCard}>
        <h1 className={styles.gateTitle}>Set your own password</h1>
        <p className={styles.gateLede}>
          You are signed in as {session.staff.name} with the password your
          manager set, which means they know it too. Until you change it, a
          sale rung up under your name is one they could also have made.
        </p>

        {error ? (
          <p className={styles.gateError} role="alert">
            {error}
          </p>
        ) : null}

        <label className={styles.gateLabel} htmlFor="current">
          The password your manager gave you
        </label>
        <input
          id="current"
          className={styles.gateInput}
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />

        <label className={styles.gateLabel} htmlFor="next">
          Your new password
        </label>
        <input
          id="next"
          className={styles.gateInput}
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
        />
        {/* Stated before they choose, not after they are rejected. The server
            enforces exactly these two and nothing else — no common-password
            list, because one that refuses a choice without saying why is how
            a password ends up on a sticky note beside the screen. */}
        <span className={styles.gateHint}>
          At least 8 characters, and not your username. You will type it at the
          start of every shift, so pick something you can type quickly.
        </span>

        <label className={styles.gateLabel} htmlFor="again">
          Type it once more
        </label>
        <input
          id="again"
          className={styles.gateInput}
          type="password"
          autoComplete="new-password"
          value={again}
          onChange={(e) => setAgain(e.target.value)}
        />

        <button
          type="button"
          className={styles.gateButton}
          disabled={busy || !current || !next || !again}
          onClick={() => void submit()}
        >
          {busy ? "Changing…" : "Change it"}
        </button>

        {/* See the banner: a till that will not open is worse than an
            attribution gap on one shift. They are asked again next time. */}
        <button
          type="button"
          className={styles.gateQuiet}
          disabled={busy}
          onClick={onDefer}
        >
          Do this later
        </button>
      </div>
    </div>
  );
}
