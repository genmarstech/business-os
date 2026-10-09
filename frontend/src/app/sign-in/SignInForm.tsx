"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import styles from "./page.module.css";

/**
 * The form itself.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * IT POSTS FROM THE BROWSER, NOT THROUGH A SERVER ACTION, AND THAT IS LOAD-
 * BEARING.
 *
 * Every other write in this application goes through a server action, which
 * is the right default. This one must not, because Django's response carries
 * two Set-Cookie headers the BROWSER has to receive: the session, and the
 * `csrftoken` that every subsequent write echoes back.
 *
 * A server action would have the Next server make that request, read the
 * body, and discard the cookies — which is exactly the bug `lib/api.ts`
 * records in its own banner: /auth/me looked like the source of the CSRF
 * cookie and was not, because the Next server calls it, so every subscriber
 * write failed with "CSRF cookie not set" until the sign-on callback started
 * minting it. Same trap, one tier down.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── THE REFUSAL IS ONE SENTENCE FOR EVERY CAUSE ────────────────────────────
 * The server answers identically whether the username is unknown, the
 * password is wrong, the login is withdrawn, the account is locked or the
 * business number is nobody's. This screen must not undo that from the
 * outside by being more helpful — it is reachable by anyone who can reach the
 * shop's URL, and a varying answer is a way to enumerate somebody's staff.
 */
export function SignInForm() {
  const router = useRouter();

  const [organisation, setOrganisation] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");

    try {
      const response = await fetch("/auth/staff/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          organization: Number(organisation),
          username,
          password,
        }),
        cache: "no-store",
      });

      if (!response.ok) {
        let detail = "That sign-in did not work. Check and try again.";
        try {
          const body = (await response.json()) as { detail?: string };
          if (body?.detail) detail = body.detail;
        } catch {
          // A non-JSON body means something upstream of Django answered.
          // The generic sentence is the right thing to show either way.
        }
        setError(detail);
        setPassword("");
        return;
      }

      /*
       * `refresh` before `replace`, and both.
       *
       * Every office page is server-rendered and `force-dynamic`, but the
       * router still holds a client-side cache of what it fetched while
       * nobody was signed in. Navigating without refreshing can land on the
       * signed-out front door that was cached a moment ago, which reads as
       * the sign-in having silently failed.
       */
      router.refresh();
      router.replace("/");
    } catch {
      setError("Could not reach the system. Check the connection and retry.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}

      <label className={styles.label} htmlFor="organisation">
        Business number
      </label>
      <input
        id="organisation"
        className={`${styles.input} ${styles.mono}`}
        inputMode="numeric"
        autoComplete="off"
        autoFocus
        required
        value={organisation}
        onChange={(e) => setOrganisation(e.target.value.replace(/\D/g, ""))}
      />
      <span className={styles.hint}>
        The number for the business you work for. It is in the email your
        manager sent you; ask them if you do not have it.
      </span>

      <label className={styles.label} htmlFor="username">
        Username
      </label>
      <input
        id="username"
        className={`${styles.input} ${styles.mono}`}
        autoCapitalize="none"
        autoCorrect="off"
        autoComplete="username"
        required
        value={username}
        onChange={(e) => setUsername(e.target.value)}
      />

      <label className={styles.label} htmlFor="password">
        Password
      </label>
      <input
        id="password"
        className={styles.input}
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />

      <button
        className={styles.submit}
        type="submit"
        disabled={busy || !organisation || !username || !password}
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>

      {/*
        Setting up and resetting both happen at the till screen, which already
        has both flows and the code machinery behind them. Pointing at it is
        better than a second copy here that would drift.
      */}
      <p className={styles.aside}>
        First time here, or forgotten your password? Both are on the{" "}
        <a href="/till">till screen</a> — set a password there, then come back.
      </p>
    </form>
  );
}
