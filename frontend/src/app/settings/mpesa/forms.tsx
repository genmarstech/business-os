"use client";

import { useActionState, useState } from "react";

import { saveMpesaTill, type State } from "./actions";
import styles from "./mpesa.module.css";

const EMPTY: State = null;

export type Till = {
  id: number;
  environment: "sandbox" | "production";
  short_code: string;
  transaction_type: string;
  account_reference: string;
  is_active: boolean;
  is_complete: boolean;
  consumer_key_set: boolean;
  consumer_secret_set: boolean;
  passkey_set: boolean;
};

/**
 * The shop's Daraja details.
 *
 * ── THE SECRETS ARE NEVER SHOWN BACK, SO THE FIELDS SAY SO ────────────────
 * Each one is blank with a note saying whether something is on file. A
 * masked value would imply the server will hand it back if asked, and it
 * will not: these are write-only all the way down.
 */
export function MpesaForm({
  organizationId,
  till,
}: {
  organizationId: number;
  till: Till | null;
}) {
  const [state, action, pending] = useActionState(saveMpesaTill, EMPTY);
  const [live, setLive] = useState(till?.environment === "production");

  const error = (field: string) => state?.field?.[field];

  return (
    <form action={action} className={styles.form}>
      {till ? <input type="hidden" name="id" value={till.id} /> : null}
      <input type="hidden" name="organization" value={organizationId} />

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Where the money goes</legend>

        <div className={styles.row}>
          <label className={styles.field}>
            <span className={styles.label}>Paybill or till number</span>
            <input
              name="short_code"
              className={styles.input}
              required
              inputMode="numeric"
              defaultValue={till?.short_code}
              placeholder="174379"
            />
            {error("short_code") ? (
              <span className={styles.error}>{error("short_code")}</span>
            ) : null}
          </label>

          <label className={styles.field}>
            <span className={styles.label}>Kind</span>
            <select
              name="transaction_type"
              className={styles.input}
              defaultValue={till?.transaction_type ?? "CustomerPayBillOnline"}
            >
              <option value="CustomerPayBillOnline">Paybill</option>
              <option value="CustomerBuyGoodsOnline">Buy goods (till)</option>
            </select>
          </label>
        </div>

        <label className={styles.field}>
          <span className={styles.label}>Account reference</span>
          <input
            name="account_reference"
            className={styles.input}
            maxLength={24}
            defaultValue={till?.account_reference}
            placeholder="Your shop's name"
          />
          <span className={styles.hint}>
            What the customer sees on their statement. Paybill only — buy-goods
            numbers have no account.
          </span>
        </label>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Your Daraja credentials</legend>
        <p className={styles.hint}>
          From your app on the Safaricom developer portal. They are stored
          encrypted and are never shown again, here or anywhere else — leave a
          box blank to keep what is already saved.
        </p>

        <Secret
          name="consumer_key"
          label="Consumer key"
          onFile={till?.consumer_key_set}
          error={error("consumer_key")}
        />
        <Secret
          name="consumer_secret"
          label="Consumer secret"
          onFile={till?.consumer_secret_set}
          error={error("consumer_secret")}
        />
        <Secret
          name="passkey"
          label="Passkey"
          onFile={till?.passkey_set}
          error={error("passkey")}
        />
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Going live</legend>

        <label className={styles.field}>
          <span className={styles.label}>Environment</span>
          <select
            name="environment"
            className={styles.input}
            defaultValue={till?.environment ?? "sandbox"}
            onChange={(e) => setLive(e.target.value === "production")}
          >
            <option value="sandbox">Sandbox — test numbers, no real money</option>
            <option value="production">Production — real money</option>
          </select>
        </label>

        {/*
          Said at the moment of choosing, not in a paragraph above that
          somebody has already scrolled past. Production is the setting where
          a wrong short code debits real customers.
        */}
        {live ? (
          <p className={styles.warn} role="alert">
            Production moves real money out of real customers&rsquo; accounts.
            Check the paybill number is yours before you turn this on.
          </p>
        ) : null}

        <label className={styles.check}>
          <input
            type="checkbox"
            name="is_active"
            defaultChecked={till?.is_active}
          />
          Offer M-Pesa at the till
        </label>
        {error("is_active") ? (
          <span className={styles.error}>{error("is_active")}</span>
        ) : null}
      </fieldset>

      {state?.general?.length ? (
        <p className={styles.error} role="alert">
          {state.general[0]}
        </p>
      ) : null}

      <button type="submit" className={styles.primary} disabled={pending}>
        {pending ? "Saving…" : "Save"}
      </button>
    </form>
  );
}

function Secret({
  name,
  label,
  onFile,
  error,
}: {
  name: string;
  label: string;
  onFile?: boolean;
  error?: string;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.label}>
        {label}{" "}
        <span className={onFile ? styles.onFile : styles.notOnFile}>
          {onFile ? "saved" : "not entered"}
        </span>
      </span>
      <input
        name={name}
        className={styles.input}
        type="password"
        autoComplete="off"
        placeholder={onFile ? "Leave blank to keep it" : ""}
      />
      {error ? <span className={styles.error}>{error}</span> : null}
    </label>
  );
}
