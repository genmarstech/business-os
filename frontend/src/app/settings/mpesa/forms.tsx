"use client";

import { useActionState, useState } from "react";

import { saveMpesaTill, type State } from "./actions";
import styles from "./mpesa.module.css";

const EMPTY: State = null;

export type Branch = { id: number; branch_name: string };

export type Till = {
  id: number;
  branch: number | null;
  environment: "sandbox" | "production";
  short_code: string;
  store_number: string;
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
  branches,
}: {
  organizationId: number;
  till: Till | null;
  /** Empty where the business has one shop — the picker is then pointless. */
  branches: Branch[];
}) {
  const [state, action, pending] = useActionState(saveMpesaTill, EMPTY);
  const [live, setLive] = useState(till?.environment === "production");
  /*
   * ── THE STORE NUMBER ONLY EXISTS FOR ONE OF THE TWO KINDS ──────────────
   * Shown for buy-goods and hidden for a paybill, because a paybill has no
   * second number and a box asking for one invites somebody to invent it.
   * Held in state rather than keyed off `till?.transaction_type`, so the
   * field appears the moment the kind is changed and not after a save.
   */
  const [buyGoods, setBuyGoods] = useState(
    till?.transaction_type === "CustomerBuyGoodsOnline",
  );

  const error = (field: string) => state?.field?.[field];

  return (
    <form action={action} className={styles.form}>
      {till ? <input type="hidden" name="id" value={till.id} /> : null}
      <input type="hidden" name="organization" value={organizationId} />

      {/*
        ── WHO THIS NUMBER IS FOR ────────────────────────────────────────
        Only shown where there is more than one shop. A business with a
        single branch has nothing to choose, and a picker offering the
        choice invites somebody to attach their only number to their only
        branch — which works, and then silently stops working the day they
        open a second shop with no default behind it.
      */}
      {branches.length > 1 ? (
        <fieldset className={styles.fieldset}>
          <legend className={styles.legend}>Who is paid on this number</legend>
          <label className={styles.field}>
            <span className={styles.label}>Applies to</span>
            <select
              name="branch"
              className={styles.input}
              defaultValue={till?.branch ? String(till.branch) : ""}
            >
              <option value="">
                Every shop — the business default
              </option>
              {branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.branch_name} only
                </option>
              ))}
            </select>
            <span className={styles.hint}>
              Most businesses are paid on one number everywhere; leave this
              on the default. Set a shop only where Safaricom gave that shop
              its own till, and add the default first — a branch without one
              behind it has no M-Pesa at all.
            </span>
            {error("branch") ? (
              <span className={styles.error}>{error("branch")}</span>
            ) : null}
          </label>
        </fieldset>
      ) : (
        <input type="hidden" name="branch" value="" />
      )}

      <fieldset className={styles.fieldset}>
        <legend className={styles.legend}>Where the money goes</legend>

        <div className={styles.row}>
          <label className={styles.field}>
            <span className={styles.label}>
              {buyGoods ? "Till number" : "Paybill number"}
            </span>
            <input
              name="short_code"
              className={styles.input}
              required
              inputMode="numeric"
              defaultValue={till?.short_code}
              placeholder="174379"
            />
            <span className={styles.hint}>
              {buyGoods
                ? "The number the money is paid into."
                : "The number a customer would normally pay."}
            </span>
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
              onChange={(e) =>
                setBuyGoods(e.target.value === "CustomerBuyGoodsOnline")
              }
            >
              <option value="CustomerPayBillOnline">Paybill</option>
              <option value="CustomerBuyGoodsOnline">Buy goods (till)</option>
            </select>
          </label>
        </div>

        {/*
          Safaricom issue a buy-goods merchant TWO numbers and the passkey is
          tied to the store one. Sending the till number in its place is
          answered with "Merchant does not exist", which reads like the
          credentials are wrong and sends somebody re-typing a passkey that
          was always correct.
        */}
        {buyGoods ? (
          <label className={styles.field}>
            <span className={styles.label}>Store number</span>
            <input
              name="store_number"
              className={styles.input}
              inputMode="numeric"
              defaultValue={till?.store_number}
              placeholder="Head office number"
            />
            <span className={styles.hint}>
              The store or head office number your passkey was issued against
              — not always the same as the till number. If Safaricom gave you
              only one number, put it here too.
            </span>
            {error("store_number") ? (
              <span className={styles.error}>{error("store_number")}</span>
            ) : null}
          </label>
        ) : null}

        {!buyGoods ? (
          <label className={styles.field}>
            <span className={styles.label}>Account reference</span>
            <input
              name="account_reference"
              className={styles.input}
              maxLength={12}
              defaultValue={till?.account_reference}
              placeholder="Your shop's name"
            />
            <span className={styles.hint}>
              What the customer sees on their statement. Twelve characters —
              M-Pesa cuts anything longer, so it is cut here where you can see
              it happen.
            </span>
          </label>
        ) : null}
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
