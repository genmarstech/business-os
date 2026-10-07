"use client";

import { useState } from "react";
import { ChooseAPassword } from "./ChooseAPassword";

import { call, rememberedOrganisation, readError, save, type TillSession } from "./session";
import styles from "./till.module.css";

/**
 * A cashier opening the till.
 *
 * ── THE BUSINESS NUMBER IS ASKED FOR, NOT CHOSEN FROM A LIST ───────────────
 * There is no endpoint that lists every business on the platform and there
 * must not be: a dropdown of every shop Genmars sells to, on a screen anybody
 * can reach, is a customer list published for free. The terminal remembers
 * the number after the first sign-in, so a cashier types it once ever.
 *
 * ── AND THE REFUSAL IS ALWAYS THE SAME SENTENCE ────────────────────────────
 * Wrong username, wrong password, withdrawn, locked and no such shop all read
 * identically, because the backend answers all five identically — see the
 * banner on AuthError. A screen that helpfully distinguished them would undo
 * that from the outside.
 */
export function SignIn({ onSignedIn }: { onSignedIn: (s: TillSession) => void }) {
  const [organisation, setOrganisation] = useState(rememberedOrganisation());
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Which door, or none. "First time here?" exists because a new employee is
  // told by email to press something, and the only button there was described
  // a problem they do not have — see the banner on ChooseAPassword.
  const [coding, setCoding] = useState<"setup" | "forgot" | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");

    try {
      const session = await call<TillSession>("/auth/staff/sign-in", {
        method: "POST",
        body: {
          organization: Number(organisation),
          username,
          password,
        },
        // Explicitly none: whatever this terminal was holding is not what is
        // being presented here, and sending it would authenticate the old
        // cashier instead of the new one.
        token: "",
      });
      save(session);
      onSignedIn(session);
    } catch (caught) {
      setError(
        readError(caught, "That sign-in did not work. Check and try again."),
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  /*
   * The organisation and username carry across, so somebody who has already
   * typed them does not type them again. A till is a touchscreen and the
   * organisation is a number nobody remembers.
   */
  if (coding) {
    return (
      <ChooseAPassword
        mode={coding}
        organisation={organisation}
        username={username}
        // A completed first-time setup comes back with a session, so this
        // goes straight to work rather than back to a form.
        onSignedIn={onSignedIn}
        onDone={() => setCoding(null)}
        onCancel={() => setCoding(null)}
      />
    );
  }

  return (
    <div className={styles.gate}>
      <form className={styles.gateCard} onSubmit={submit}>
        <h1 className={styles.gateTitle}>Open the till</h1>
        <p className={styles.gateLede}>
          Your sign-in belongs to the business you work for. It is not a
          Genmars account and your password never reaches Genmars. If you have
          just been set up, or have forgotten it, the two buttons below the
          sign-in will email you a code.
        </p>

        {error ? (
          <p className={styles.gateError} role="alert">
            {error}
          </p>
        ) : null}

        <label className={styles.gateLabel} htmlFor="organisation">
          Business number
        </label>
        <input
          id="organisation"
          className={`${styles.gateInput} ${styles.mono}`}
          inputMode="numeric"
          required
          value={organisation}
          onChange={(e) => setOrganisation(e.target.value)}
        />
        <span className={styles.gateHint}>
          Asked once. This terminal remembers it.
        </span>

        <label className={styles.gateLabel} htmlFor="username">
          Username
        </label>
        <input
          id="username"
          className={`${styles.gateInput} ${styles.mono}`}
          autoComplete="username"
          autoFocus
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label className={styles.gateLabel} htmlFor="password">
          Password
        </label>
        <input
          id="password"
          className={styles.gateInput}
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <button className={styles.gateButton} type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {/*
          Below the sign-in button, not beside the password field. Somebody
          reaches for either of these after the form has not worked, and the
          alternative — a manager walking over to type a password — is the
          thing both of them replace.

          First time above forgotten, because the person who needs it has
          never seen this screen before and is looking for permission to be
          here. The wording is the whole point: the invitation email tells
          them to tap these exact words.
        */}
        <button
          type="button"
          className={styles.gateQuiet}
          disabled={busy}
          onClick={() => setCoding("setup")}
        >
          First time here?
        </button>

        <button
          type="button"
          className={styles.gateQuiet}
          disabled={busy}
          onClick={() => setCoding("forgot")}
        >
          Forgotten your password?
        </button>
      </form>
    </div>
  );
}
