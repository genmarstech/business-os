"use client";

import { useActionState, useState } from "react";

import { cancelSubscription, reinstateSubscription, type State } from "./actions";
import styles from "./subscription.module.css";

const EMPTY: State = null;

/**
 * Ending the arrangement.
 *
 * ── IT ASKS, AND IT SAYS WHAT HAPPENS ─────────────────────────────────────
 * Behind a disclosure rather than a bare button, because the click is hard
 * to take back and easy to make by accident on a settings page. The copy
 * states the two things somebody needs before deciding: the period they
 * have paid for is theirs to the end of it, and nothing stops when they
 * click.
 */
export function CancelForm({
  id,
  coveredUntil,
}: {
  id: number;
  coveredUntil: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [state, action, pending] = useActionState(cancelSubscription, EMPTY);

  if (!open) {
    return (
      <button
        type="button"
        className={styles.quietButton}
        onClick={() => setOpen(true)}
      >
        Cancel this subscription
      </button>
    );
  }

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="id" value={id} />
      <p className={styles.formLede}>
        {coveredUntil
          ? `Nothing stops now. You keep everything you have paid for, to ${coveredUntil}, and your tills, sales and stock carry on either way.`
          : "Nothing stops now. Your tills, sales and stock carry on either way."}
      </p>
      <label className={styles.label} htmlFor="cancel-reason">
        Why, if you would like to say
      </label>
      <input
        id="cancel-reason"
        name="reason"
        className={styles.input}
        placeholder="Optional"
        maxLength={300}
      />
      {state?.general?.length ? (
        <p className={styles.error}>{state.general[0]}</p>
      ) : null}
      <div className={styles.actions}>
        <button type="submit" className={styles.danger} disabled={pending}>
          {pending ? "Cancelling…" : "Cancel the subscription"}
        </button>
        <button
          type="button"
          className={styles.quietButton}
          onClick={() => setOpen(false)}
        >
          Keep it
        </button>
      </div>
    </form>
  );
}

export function ReinstateForm({ id }: { id: number }) {
  const [state, action, pending] = useActionState(reinstateSubscription, EMPTY);

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="id" value={id} />
      {state?.general?.length ? (
        <p className={styles.error}>{state.general[0]}</p>
      ) : null}
      <button type="submit" className={styles.primary} disabled={pending}>
        {pending ? "Reinstating…" : "Reinstate it"}
      </button>
    </form>
  );
}
