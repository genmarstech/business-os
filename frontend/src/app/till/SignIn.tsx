"use client";

import { useState } from "react";

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

  return (
    <div className={styles.gate}>
      <form className={styles.gateCard} onSubmit={submit}>
        <h1 className={styles.gateTitle}>Open the till</h1>
        <p className={styles.gateLede}>
          Your sign-in belongs to the business you work for. It is not a
          Genmars account and your password never reaches Genmars — if you
          have forgotten it, your manager sets you a new one.
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
      </form>
    </div>
  );
}
