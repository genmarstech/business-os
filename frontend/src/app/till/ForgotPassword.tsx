"use client";

import { useState } from "react";
import { call, readError } from "./session";
import styles from "./till.module.css";

/**
 * A cashier who has forgotten the till password.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE SCREEN MUST NOT SAY MORE THAN THE SERVER DOES.
 *
 * The request endpoint answers identically whether the username exists, is
 * deactivated, has no email on file, or was rate limited — because a till
 * sign-in screen is reachable by anyone who can reach the shop's URL, and a
 * varying answer would let a stranger enumerate somebody else's staff.
 *
 * So this screen moves to "enter the code" unconditionally. It must never
 * check whether an email was really sent, and must never say "no account with
 * that username" — that would hand back through the UI exactly what the API
 * is careful not to give.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Two steps in one component rather than two routes: a till is a kiosk, often
 * without a usable back button, and a person who has just been sent a code
 * should not be able to lose the form it goes into by mistyping a URL.
 */
export function ForgotPassword({
  organisation,
  username: initialUsername,
  onDone,
  onCancel,
}: {
  organisation: string;
  username: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [step, setStep] = useState<"ask" | "enter">("ask");
  const [username, setUsername] = useState(initialUsername);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function ask() {
    setBusy(true);
    setError("");
    try {
      await call<void>("/auth/staff/password/reset", {
        method: "POST",
        body: { organization: Number(organisation), username },
      });
    } catch (caught) {
      // A transport failure is worth showing — it is about the till's network,
      // not about whether the username exists.
      setError(readError(caught, "Could not reach the shop's records."));
      setBusy(false);
      return;
    }
    setBusy(false);
    setStep("enter");
  }

  async function confirm() {
    setError("");
    if (password !== again) {
      setError("Those two do not match. Type the new one again.");
      return;
    }

    setBusy(true);
    try {
      await call<void>("/auth/staff/password/reset/confirm", {
        method: "POST",
        body: {
          organization: Number(organisation),
          username,
          code: code.trim(),
          new_password: password,
        },
      });
      setDone(true);
    } catch (caught) {
      // The server's words. It says one thing for every way a code can be
      // refused, and the only message it varies is about the password just
      // typed — which is the one the person can act on.
      setError(readError(caught, "That did not work. Ask for a new code."));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className={styles.gate}>
        <div className={styles.gateCard}>
          <h1 className={styles.gateTitle}>Password changed</h1>
          <p className={styles.gateLede}>
            Sign in with the one you just chose. Any other till still signed in
            as you has been signed out.
          </p>
          <button type="button" className={styles.gateButton} onClick={onDone}>
            Back to sign in
          </button>
        </div>
      </div>
    );
  }

  if (step === "ask") {
    return (
      <div className={styles.gate}>
        <div className={styles.gateCard}>
          <h1 className={styles.gateTitle}>Forgotten password</h1>
          <p className={styles.gateLede}>
            We will email a code to the address your manager has on file for
            you. If you do not know which address that is, ask them.
          </p>

          {error ? (
            <p className={styles.gateError} role="alert">
              {error}
            </p>
          ) : null}

          <label className={styles.gateLabel} htmlFor="forgot-username">
            Your username
          </label>
          <input
            id="forgot-username"
            className={`${styles.gateInput} ${styles.mono}`}
            autoCapitalize="none"
            autoCorrect="off"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />

          <button
            type="button"
            className={styles.gateButton}
            disabled={busy || !username.trim()}
            onClick={() => void ask()}
          >
            {busy ? "Sending…" : "Send me a code"}
          </button>

          <button type="button" className={styles.gateQuiet} onClick={onCancel}>
            Back to sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.gate}>
      <div className={styles.gateCard}>
        <h1 className={styles.gateTitle}>Enter the code</h1>
        {/*
          "If there is an account" — the same hedge the API uses, and for the
          same reason. Saying "we have sent you a code" would confirm the
          username exists to anybody who typed a guess.
        */}
        <p className={styles.gateLede}>
          If there is an account for that username with an email address on
          file, a six-digit code is on its way. It works once and expires in 15
          minutes.
        </p>

        {error ? (
          <p className={styles.gateError} role="alert">
            {error}
          </p>
        ) : null}

        <label className={styles.gateLabel} htmlFor="code">
          The code from your email
        </label>
        <input
          id="code"
          className={`${styles.gateInput} ${styles.mono}`}
          // Digits, on a touchscreen, so the numeric keypad rather than the
          // full keyboard. `autoComplete="one-time-code"` lets a phone offer
          // it — harmless here and a real saving where the mail is on the
          // same device.
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
        />

        <label className={styles.gateLabel} htmlFor="reset-password">
          Your new password
        </label>
        <input
          id="reset-password"
          className={styles.gateInput}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <span className={styles.gateHint}>
          At least 8 characters, and not your username. You will type it at the
          start of every shift, so pick something you can type quickly.
        </span>

        <label className={styles.gateLabel} htmlFor="reset-again">
          Type it once more
        </label>
        <input
          id="reset-again"
          className={styles.gateInput}
          type="password"
          autoComplete="new-password"
          value={again}
          onChange={(e) => setAgain(e.target.value)}
        />

        <button
          type="button"
          className={styles.gateButton}
          disabled={busy || code.length !== 6 || !password || !again}
          onClick={() => void confirm()}
        >
          {busy ? "Setting it…" : "Set my password"}
        </button>

        <button
          type="button"
          className={styles.gateQuiet}
          disabled={busy}
          onClick={() => setStep("ask")}
        >
          Send another code
        </button>
      </div>
    </div>
  );
}
