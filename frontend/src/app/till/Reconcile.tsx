"use client";

import { useCallback, useEffect, useState } from "react";

import { call, readError } from "./session";
import styles from "./till.module.css";

/**
 * Did that M-Pesa payment actually go through?
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE QUESTION A CASHIER CANNOT ANSWER, AND THE CUSTOMER CAN SEE THEM
 * FAILING TO.
 *
 * A till screen is only ever right about the push it is holding right now.
 * Everything else a cashier might need to know — the payment from two
 * customers ago that the screen said failed, the one where the sale was
 * refused after the money arrived, the one where somebody closed the tab —
 * was invisible from the register. The only way to find out was to ask the
 * office to look, with a queue waiting.
 *
 * All three of those happened in one afternoon on production, so this is
 * not a hypothetical convenience:
 *
 *   · a push settled "failed" on an answer that meant "still going", while
 *     the customer's money was on its way
 *   · a push confirmed paid whose checkout request never reached the server
 *   · a push confirmed paid for a basket that could not be sold at this
 *     branch at all
 *
 * In each case the shop HAD the money and the till said otherwise.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * ⚠ NOTHING ON THIS SCREEN DECIDES WHETHER A PAYMENT HAPPENED.
 *
 * There is no button that marks a push paid, and there must never be. "Ask
 * M-Pesa again" posts to `recheck`, which clears the settlement and lets
 * the server put back whatever Safaricom answers — the same rule that makes
 * the public callback a hint rather than a verdict. A cashier who could
 * declare a payment received is a cashier who can be talked into it by
 * somebody holding a convincing SMS.
 * ══════════════════════════════════════════════════════════════════════════
 */

type Push = {
  id: number;
  amount: string;
  phone_number: string;
  status: "requested" | "paid" | "failed" | "expired";
  status_label: string;
  mpesa_receipt: string;
  result_description: string;
  sale: number | null;
  created_at: string;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T>): T[] {
  return Array.isArray(page) ? page : (page.results ?? []);
}

function when(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", {
    timeZone: "Africa/Nairobi",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * What this row means, in a sentence a cashier can act on.
 *
 * The status alone is not the answer. "Paid" with a sale attached is a
 * finished transaction and needs nobody; "paid" with none is money the shop
 * is holding against no sale, which is the whole reason this screen exists.
 */
function verdict(push: Push): { tone: string; says: string } {
  if (push.status === "paid" && push.sale !== null) {
    return { tone: "good", says: "Paid, and the sale is recorded." };
  }
  if (push.status === "paid") {
    return {
      tone: "attend",
      says:
        "Paid, but no sale was written. The shop has this money. " +
        "Tell a manager so it can be put against a sale.",
    };
  }
  if (push.status === "requested") {
    return { tone: "wait", says: "Still waiting for the customer." };
  }
  return {
    tone: "bad",
    says: push.result_description || "Not paid.",
  };
}

export function Reconcile({
  branchId,
  onClose,
}: {
  branchId: number | null;
  onClose: () => void;
}) {
  const [pushes, setPushes] = useState<Push[] | null>(null);
  const [error, setError] = useState("");
  const [asking, setAsking] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await call<Page<Push>>("/pay/mpesa/pushes/?limit=20");
      // Newest first. The one a cashier is asking about is almost always
      // the last one, and making them scroll for it is the opposite of
      // what this screen is for.
      setPushes(
        rows(page)
          .slice()
          .sort((a, b) => b.id - a.id),
      );
      setError("");
    } catch (caught) {
      setPushes([]);
      setError(readError(caught, "Could not read today's payments."));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function askAgain(push: Push) {
    setAsking(push.id);
    setError("");
    try {
      const fresh = await call<Push>(`/pay/mpesa/pushes/${push.id}/recheck/`, {
        method: "POST",
      });
      setPushes((current) =>
        (current ?? []).map((row) => (row.id === fresh.id ? fresh : row)),
      );
    } catch (caught) {
      setError(readError(caught, "Could not reach M-Pesa just now."));
    } finally {
      setAsking(null);
    }
  }

  return (
    <div className={styles.tool} role="dialog" aria-label="Check a payment">
      <h2 className={styles.toolTitle}>Check a payment</h2>
      <p className={styles.toolLede}>
        Every M-Pesa request from this till. &ldquo;Ask M-Pesa again&rdquo;
        puts the question to Safaricom now — it cannot change what happened,
        only find out.
      </p>

      {error ? (
        <p className={styles.gateError} role="alert">
          {error}
        </p>
      ) : null}

      {pushes === null ? (
        <p className={styles.toolLede}>Looking…</p>
      ) : pushes.length === 0 ? (
        <p className={styles.toolLede}>
          No M-Pesa payments have been asked for yet.
        </p>
      ) : (
        <ul className={styles.recList}>
          {pushes.map((push) => {
            const said = verdict(push);
            return (
              <li key={push.id} className={styles.recRow}>
                <div className={styles.recTop}>
                  <span className={styles.recAmount}>
                    {Number(push.amount).toFixed(2)}
                  </span>
                  <span className={styles.recPhone}>{push.phone_number}</span>
                  <span className={styles.recWhen}>
                    {when(push.created_at)}
                  </span>
                </div>
                <div
                  className={`${styles.recVerdict} ${
                    styles[`rec_${said.tone}`] ?? ""
                  }`}
                >
                  {said.says}
                </div>
                {push.mpesa_receipt ? (
                  <div className={styles.recCode}>
                    M-Pesa code {push.mpesa_receipt}
                  </div>
                ) : null}
                {/*
                  Offered on anything not already finished. A paid push with
                  a sale against it is settled business; asking again about
                  it would be a button that can only confirm what the row
                  already says.
                */}
                {push.status === "paid" && push.sale !== null ? null : (
                  <button
                    className={styles.recAsk}
                    onClick={() => void askAgain(push)}
                    disabled={asking !== null}
                  >
                    {asking === push.id ? "Asking…" : "Ask M-Pesa again"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <button className={styles.recClose} onClick={onClose}>
        Close
      </button>
    </div>
  );
}
