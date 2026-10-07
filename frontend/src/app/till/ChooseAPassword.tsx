"use client";

import { useState } from "react";
import { call, readError, save, type TillSession } from "./session";
import styles from "./till.module.css";

/**
 * Choosing a till password from a code, on a first day or after forgetting one.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * TWO DOORS, ONE SCREEN, AND THE DOOR IS WHY IT MATTERS.
 *
 * This was "Forgotten password" only, and a new employee was told by email to
 * tap it. They have not forgotten anything — they have never had a password —
 * and a person on their first morning, told to press a button that describes
 * a problem they do not have, asks their manager instead. Which is the moment
 * the manager types a password and reads it out, which is the habit this
 * whole path exists to replace.
 *
 * So `mode` changes the words and the ending, and nothing else: the endpoints
 * are the same two, because the server decides what kind of code a username
 * gets from whether that login has ever had a password. A client that chose
 * would be a client that could ask for the long-lived code against a live
 * credential.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── THE SCREEN MUST NOT SAY MORE THAN THE SERVER DOES ────────────────────
 * The request endpoint answers identically whether the username exists, is
 * deactivated, has no email on file, or was rate limited — because this
 * screen is reachable by anyone who can reach the shop's URL, and a varying
 * answer would let a stranger enumerate somebody else's staff.
 *
 * So it moves to "enter the code" unconditionally. It must never check
 * whether an email was really sent, never say "no account with that
 * username", and never name how long the code lasts — a first-time code runs
 * for days and a reset for fifteen minutes, so stating the window would say
 * which kind of login the username belongs to. The email states it, and the
 * email goes to the person it concerns.
 *
 * Two steps in one component rather than two routes: a till is a kiosk, often
 * without a usable back button, and somebody who has just been sent a code
 * should not be able to lose the form it goes into by mistyping a URL.
 */

const WORDS = {
  setup: {
    askTitle: "First time here",
    askLede:
      "Your manager has set you up. We will email a setup code to the address they have on file for you — if you do not know which address that is, ask them.",
    askButton: "Email me a setup code",
    enterTitle: "Set your password",
    enterLede:
      "If that username has been set up with an email address on file, a six-digit code is on its way. The email says how long it lasts.",
    codeLabel: "The setup code from your email",
    submit: "Start work",
  },
  forgot: {
    askTitle: "Forgotten password",
    askLede:
      "We will email a code to the address your manager has on file for you. If you do not know which address that is, ask them.",
    askButton: "Send me a code",
    enterTitle: "Enter the code",
    enterLede:
      "If there is an account for that username with an email address on file, a six-digit code is on its way. The email says how long it lasts.",
    codeLabel: "The code from your email",
    submit: "Set my password",
  },
} as const;

export function ChooseAPassword({
  mode,
  organisation,
  username: initialUsername,
  onSignedIn,
  onDone,
  onCancel,
}: {
  mode: "setup" | "forgot";
  organisation: string;
  username: string;
  /**
   * Straight to work, when the server hands back a session.
   *
   * It does that for a first-time setup and not for a reset, and the
   * difference is argued in CompletePasswordResetView: a reset has a password
   * they used to know, so re-typing the new one at the sign-in screen is how
   * they find out they have replaced it. A setup has no such prior, and this
   * is somebody's first morning in a shop.
   */
  onSignedIn: (session: TillSession) => void;
  onDone: () => void;
  onCancel: () => void;
}) {
  const words = WORDS[mode];

  /*
   * ── IT ASKS FOR THE BUSINESS NUMBER WHEN IT DOES NOT HAVE ONE ───────────
   *
   * The number comes from the sign-in form, which remembers it after the
   * first shift. A FIRST-TIME person is the one case where there is nothing
   * to remember: a new terminal, or a new employee on a phone, arrives here
   * with an empty field — and this screen posted `Number("")`, which is 0,
   * which matches no shop. The request then answered its usual uniform
   * "a code is on its way", and the code never came, with nothing on screen
   * to correct.
   *
   * Their invitation email carries the number for exactly this reason, so
   * the field is here rather than sending them back to a form they have not
   * got a reason to fill in yet.
   */
  const [org, setOrg] = useState(organisation);

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
        body: { organization: Number(org), username },
        // Explicitly none. Whatever this terminal is holding belongs to
        // somebody else's shift and is not what is being presented here.
        token: "",
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
      /*
       * 204 on a reset, 201 with a session on a first-time setup. `call`
       * returns undefined for the first, so this branches on what came back
       * rather than on `mode` — the server decides which kind of code the
       * username got, and a client that decided instead could disagree with
       * it and drop a perfectly good session on the floor.
       */
      const session = await call<TillSession | undefined>(
        "/auth/staff/password/reset/confirm",
        {
          method: "POST",
          body: {
            organization: Number(org),
            username,
            code: code.trim(),
            new_password: password,
          },
          token: "",
        },
      );

      if (session?.token) {
        save(session);
        onSignedIn(session);
        return;
      }
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
          <h1 className={styles.gateTitle}>{words.askTitle}</h1>
          <p className={styles.gateLede}>{words.askLede}</p>

          {error ? (
            <p className={styles.gateError} role="alert">
              {error}
            </p>
          ) : null}

          <label className={styles.gateLabel} htmlFor="code-organisation">
            Business number
          </label>
          <input
            id="code-organisation"
            className={`${styles.gateInput} ${styles.mono}`}
            inputMode="numeric"
            // The first empty field takes the cursor. On a returning terminal
            // the number is already filled in, so the caret belongs on the
            // username instead.
            autoFocus={!organisation}
            value={org}
            onChange={(e) => setOrg(e.target.value.replace(/\D/g, ""))}
          />
          <span className={styles.gateHint}>
            {mode === "setup"
              ? "In your email, above your username."
              : "The same one you sign in with. Ask your manager if you do not have it."}
          </span>

          <label className={styles.gateLabel} htmlFor="code-username">
            Your username
          </label>
          <input
            id="code-username"
            className={`${styles.gateInput} ${styles.mono}`}
            autoCapitalize="none"
            autoCorrect="off"
            autoFocus={Boolean(organisation)}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />

          <button
            type="button"
            className={styles.gateButton}
            disabled={busy || !username.trim() || !org.trim()}
            onClick={() => void ask()}
          >
            {busy ? "Sending…" : words.askButton}
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
        <h1 className={styles.gateTitle}>{words.enterTitle}</h1>
        <p className={styles.gateLede}>{words.enterLede}</p>

        {error ? (
          <p className={styles.gateError} role="alert">
            {error}
          </p>
        ) : null}

        <label className={styles.gateLabel} htmlFor="code">
          {words.codeLabel}
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

        <label className={styles.gateLabel} htmlFor="chosen-password">
          {mode === "setup" ? "Choose your password" : "Your new password"}
        </label>
        <input
          id="chosen-password"
          className={styles.gateInput}
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <span className={styles.gateHint}>
          At least 8 characters, and not your username. You will type it at the
          start of every shift, so pick something you can type quickly. Nobody
          else will know it — not your manager, not Genmars.
        </span>

        <label className={styles.gateLabel} htmlFor="chosen-again">
          Type it once more
        </label>
        <input
          id="chosen-again"
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
          {busy ? "Setting it…" : words.submit}
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
