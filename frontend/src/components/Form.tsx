"use client";

import { useFormStatus } from "react-dom";

import styles from "./Form.module.css";

/**
 * Form primitives.
 *
 * Client components only because they need `useFormStatus` and an `id` that
 * ties a label to its input. Nothing here holds application state — the forms
 * that use them are server actions.
 */

export function Field({
  name,
  label,
  hint,
  error,
  children,
}: {
  name: string;
  label: string;
  hint?: string;
  error?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={name}>
        {label}
      </label>
      {hint ? <span className={styles.hint}>{hint}</span> : null}
      {children}
      {/*
        role="alert" so a screen reader announces the message when it appears,
        rather than leaving somebody to discover a red border they may not be
        able to see.
      */}
      {error ? (
        <span className={styles.error} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function Text({
  name,
  label,
  hint,
  error,
  mono,
  ...rest
}: {
  name: string;
  label: string;
  hint?: string;
  error?: string;
  mono?: boolean;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <Field name={name} label={label} hint={hint} error={error}>
      <input
        id={name}
        name={name}
        className={`${styles.input} ${mono ? styles.mono : ""} ${
          error ? styles.invalid : ""
        }`}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
    </Field>
  );
}

export function Select({
  name,
  label,
  hint,
  error,
  children,
  ...rest
}: {
  name: string;
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
} & React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <Field name={name} label={label} hint={hint} error={error}>
      <select
        id={name}
        name={name}
        className={`${styles.select} ${error ? styles.invalid : ""}`}
        aria-invalid={error ? true : undefined}
        {...rest}
      >
        {children}
      </select>
    </Field>
  );
}

export function Row({ children }: { children: React.ReactNode }) {
  return <div className={styles.row}>{children}</div>;
}

export function General({ messages }: { messages: string[] }) {
  if (!messages.length) return null;
  return (
    <div className={styles.general} role="alert">
      {messages.map((m) => (
        <div key={m}>{m}</div>
      ))}
    </div>
  );
}

/**
 * The submit button, disabled while the action is in flight.
 *
 * `useFormStatus` rather than local state, so this works without the parent
 * knowing anything about it — and so a double-click cannot send the same
 * create twice. None of these writes is idempotent; the sale is the only one
 * in the application that carries a key, and it is not one of these.
 */
export function Submit({
  children,
  pending: label,
}: {
  children: React.ReactNode;
  pending?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <div className={styles.actions}>
      <button type="submit" className={styles.button} disabled={pending}>
        {pending ? (label ?? "Working…") : children}
      </button>
    </div>
  );
}
