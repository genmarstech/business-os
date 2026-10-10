"use client";

import { useMemo, useRef, useState } from "react";

import { cents, shillings } from "./money";
import { call, readError, type TillSession } from "./session";
import styles from "./return.module.css";

/**
 * Taking a return at the counter.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * A CASHIER CANNOT REFUND, AND THIS SCREEN DOES NOT CHANGE THAT.
 *
 * identity/access.py is explicit about it, and says why: voiding a sale and
 * refunding one are how a till is emptied by the person standing at it, so
 * they need a second person, and the cheapest version of a second person is
 * a permission the first one does not hold. Blueprint module 6 lists
 * "manager approvals" beside cashier access for exactly this.
 *
 * So the gap was never a missing button. Until now a customer with a receipt
 * and a wrong-size shirt had to be sent to somebody with an office login, or
 * — what actually happened — the manager recorded a `pay_out` through Cash
 * in/out, which balances the drawer and produces NO refund document: nothing
 * goes back into stock, the sale still reads as fully paid, and the line can
 * be returned again tomorrow.
 *
 * What this screen does is split the work the way the shop floor already
 * splits it. The CASHIER finds the sale, counts what came back, says where
 * it goes and why — all of it under their own session, none of it requiring
 * a permission they lack. Then a MANAGER signs in on this terminal and that
 * single request carries their credential. The manager spends fifteen
 * seconds at the counter instead of five minutes.
 *
 * ── THE MANAGER'S CREDENTIAL IS BORROWED, NOT INSTALLED ──────────────────
 *
 * ⚠ THEIR TOKEN IS NEVER SAVED AND THEIR SESSION IS CLOSED IMMEDIATELY.
 *
 *   `save()` is not called with it, so it never reaches localStorage and the
 *   terminal does not become the manager's till. The session is signed out
 *   as soon as the refund comes back — a shared terminal must not be left
 *   holding a live manager session that the next person to touch it inherits.
 *   The sign-out is deliberately NOT conditional on the refund succeeding.
 *
 *   It is held in a local for the length of one request and in a ref for the
 *   sign-out that follows. It is never put in a URL — see the banner in
 *   session.ts, which applies to this token exactly as it does to the till's.
 *
 * ── AND THE SERVER IS WHAT ENFORCES ANY OF IT ────────────────────────────
 *
 * Everything above is arrangement, not security. `sales/views.py` checks
 * `access.may(user, SALES_REFUND, branch)` on the credential that actually
 * arrives, and pins `processed_by` to whoever is signed in, so a refund
 * cannot be filed under a name other than the one that authorised it. Three
 * tests in sales/test_returns.py hold the line: a cashier's token is
 * refused, a manager's is accepted, and a manager cannot record the refund
 * against the cashier.
 * ══════════════════════════════════════════════════════════════════════════
 */

type SaleLine = {
  id: number;
  product_name: string;
  sku: string;
  quantity: string;
  line_total: string;
  /**
   * What is left to return on this line, after every completed refund
   * already written against it. The server derives it the same way it
   * enforces it — see SaleItemSerializer.
   *
   * Optional because a terminal running this build against an older API
   * would get a sale without it. `left()` falls back to the sold quantity
   * rather than to zero: refusing to return anything would be worse than
   * letting the server refuse an over-return, which it does anyway.
   */
  quantity_refundable?: string;
};

type Sale = {
  id: number;
  number: number | string;
  total: string;
  /**
   * Null for a walk-in, which is most sales.
   *
   * It decides whether "On account" is offered at all: the server refuses a
   * credit refund without a customer, because there would be no balance to
   * reduce and the money would simply have been written off while the
   * paperwork said otherwise.
   */
  customer: number | null;
  completed_at?: string | null;
  receipt?: { number?: string } | null;
  items: SaleLine[];
};

type Refund = {
  number: number;
  total: string;
  method_label?: string;
  processed_by_name?: string;
};

/** How the money goes back. `credit` is offered only on an account sale. */
const METHODS = [
  { value: "cash", label: "Cash" },
  { value: "mpesa", label: "M-Pesa" },
  { value: "card", label: "Card" },
  { value: "bank", label: "Bank" },
] as const;

function left(line: SaleLine): number {
  const raw = line.quantity_refundable ?? line.quantity;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

export function Return({
  session,
  shiftId,
  branchId,
  lastSale,
  onClose,
}: {
  session: TillSession;
  shiftId: number;
  branchId: number | null;
  /**
   * The sale rung up a moment ago, when there is one. Most returns at a
   * counter are somebody walking back in with a receipt, but "I've changed
   * my mind" seconds after paying is common enough that making it one press
   * is worth a prop.
   */
  lastSale: { id: number; number: number | string } | null;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [sale, setSale] = useState<Sale | null>(null);
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [restock, setRestock] = useState<Record<number, boolean>>({});
  const [reason, setReason] = useState("");
  const [method, setMethod] = useState<string>("cash");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<Refund | null>(null);

  /*
   * ── MINTED WHEN THE RETURN IS STARTED, NOT WHEN IT IS SENT ──────────────
   * The same rule the basket follows, for the same reason: a key generated
   * per attempt makes a retry over a bad link a SECOND refund. A manager
   * whose first press times out presses again, and the server hands back the
   * refund it already wrote.
   *
   * It is replaced only when a different sale is loaded — see `pick`.
   */
  const key = useRef(
    `till-${shiftId}-rf-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 8)}`,
  );

  /** The manager's session, for the length of one refund and no longer. */
  const borrowed = useRef<string>("");

  const onAccount = sale?.customer != null;

  const chosen = useMemo(() => {
    if (!sale) return [];
    return sale.items
      .map((line) => ({ line, quantity: Number(quantities[line.id] ?? "") }))
      .filter(({ quantity }) => Number.isFinite(quantity) && quantity > 0);
  }, [sale, quantities]);

  /*
   * ── A PREVIEW, AND THE SERVER'S FIGURE IS THE ONE THAT COUNTS ───────────
   * Pro-rata on the line as it was actually charged, which is what
   * refund_sale does — refunding the shelf price of a discounted item hands
   * back money that was never taken. Computed here only so the cashier can
   * tell the customer the amount before fetching a manager. money.ts carries
   * the rule this follows.
   */
  const preview = useMemo(
    () =>
      chosen.reduce((sum, { line, quantity }) => {
        const sold = Number(line.quantity);
        if (!Number.isFinite(sold) || sold <= 0) return sum;
        return sum + Math.round((cents(line.line_total) / sold) * quantity);
      }, 0),
    [chosen],
  );

  const tooMany = chosen.filter(({ line, quantity }) => quantity > left(line));

  async function find(byId?: number) {
    setError("");
    setDone(null);
    setBusy(true);
    try {
      let found: Sale | null = null;
      if (byId) {
        found = await call<Sale>(`/sls/sales/${byId}/`);
      } else {
        const typed = query.trim();
        if (!typed) {
          setError("Type the number from the receipt.");
          return;
        }
        const page = await call<{ results?: Sale[] } | Sale[]>(
          `/sls/sales/?number=${encodeURIComponent(typed)}`,
        );
        const list = Array.isArray(page) ? page : (page.results ?? []);
        found = list[0] ?? null;
      }

      if (!found) {
        // True, and the only honest answer: the lookup is scoped to this
        // shop and this cashier's branches, so a receipt from another shop
        // and a number that never existed are the same outcome here.
        setError("No sale with that number. Check the receipt.");
        return;
      }
      pick(found);
    } catch (caught) {
      setError(readError(caught, "Could not reach the shop's records."));
    } finally {
      setBusy(false);
    }
  }

  function pick(found: Sale) {
    setSale(found);
    setQuantities({});
    setRestock(
      Object.fromEntries(found.items.map((line) => [line.id, true])),
    );
    setReason("");
    setMethod("cash");
    // A new sale is a new return, so it gets its own key. Reusing the old
    // one would hand back the PREVIOUS refund instead of writing this one.
    key.current = `till-${shiftId}-rf-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 8)}`;
  }

  async function authorise() {
    if (!sale) return;
    setError("");

    if (chosen.length === 0) {
      setError("Enter how many of each thing came back.");
      return;
    }
    const over = tooMany[0];
    if (over) {
      setError(
        `Only ${left(over.line)} of ${over.line.product_name} can still be ` +
          `returned on this sale.`,
      );
      return;
    }
    if (!reason.trim()) {
      setError("Say why it came back. It is the only record of what happened.");
      return;
    }
    if (branchId === null) {
      setError("This till is not attached to a branch. Ask for help.");
      return;
    }
    if (!username.trim() || !password) {
      setError("A manager has to sign in to approve this.");
      return;
    }

    setBusy(true);
    try {
      /*
       * Step one: the manager's own credential, against the same shop.
       * `token: ""` authenticates nobody — whatever this terminal holds is
       * the cashier, and sending it would sign the refund as them.
       */
      const manager = await call<TillSession>("/auth/staff/sign-in", {
        method: "POST",
        body: {
          organization: session.organisation.id,
          username: username.trim(),
          password,
        },
        token: "",
      });
      borrowed.current = manager.token;

      try {
        /*
         * Step two: the refund, as the manager. If they do not hold
         * `sales.refund` at this branch the server refuses here, and
         * because the token was passed in rather than read from storage
         * that refusal does not end the CASHIER's shift — see the note in
         * session.ts, which this flow is the reason for.
         */
        const refund = await call<Refund>(`/sls/sales/${sale.id}/refund/`, {
          method: "POST",
          token: manager.token,
          body: {
            branch: branchId,
            processed_by: manager.staff.id,
            shift: shiftId,
            reason: reason.trim(),
            method,
            idempotency_key: key.current,
            lines: chosen.map(({ line, quantity }) => ({
              sale_item: line.id,
              quantity: String(quantity),
              restock: restock[line.id] !== false,
            })),
          },
        });

        setDone(refund);
        setPassword("");
        setUsername("");
      } finally {
        /*
         * Step three, and it runs whether or not the refund was written. A
         * shared terminal must not be left holding a live manager session
         * for the next person who touches it.
         */
        await signOutManager();
      }
    } catch (caught) {
      setError(
        readError(
          caught,
          "That did not go through. Check the manager's sign-in and try again.",
        ),
      );
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  async function signOutManager() {
    const token = borrowed.current;
    borrowed.current = "";
    if (!token) return;
    try {
      await call("/auth/staff/sign-out", { method: "POST", token });
    } catch {
      // Already expired, already closed, or the link went down. The session
      // expires on its own and the token was never stored, so there is
      // nothing a cashier could do about this and nothing to tell them.
    }
  }

  // ── done ────────────────────────────────────────────────────────────────
  if (done) {
    return (
      <div className={styles.wrap} role="dialog" aria-label="Return taken">
        <div className={styles.sheet}>
          <h2 className={styles.title}>Return #{done.number} recorded</h2>
          <p className={styles.amount}>
            {shillings(cents(done.total))}{" "}
            <span className={styles.amountWord}>back to the customer</span>
          </p>
          <p className={styles.lede}>
            {done.method_label ? `${done.method_label}. ` : ""}
            Approved by {done.processed_by_name ?? "a manager"}.
            {method === "cash"
              ? " The drawer is expected to hold that much less at the close."
              : " Nothing came out of the drawer."}
          </p>
          <div className={styles.actions}>
            <button type="button" className={styles.primary} onClick={onClose}>
              Back to the till
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── find a sale ─────────────────────────────────────────────────────────
  if (!sale) {
    return (
      <div className={styles.wrap} role="dialog" aria-label="Take a return">
        <div className={styles.sheet}>
          <h2 className={styles.title}>Take a return</h2>
          <p className={styles.lede}>
            Find the sale first. The number is printed on the receipt.
          </p>

          {error ? (
            <p className={styles.error} role="alert">
              {error}
            </p>
          ) : null}

          <label className={styles.label} htmlFor="return-number">
            Receipt or sale number
          </label>
          <div className={styles.findRow}>
            <input
              id="return-number"
              className={`${styles.input} ${styles.mono}`}
              inputMode="numeric"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void find();
              }}
              placeholder="1042"
            />
            <button
              type="button"
              className={styles.primary}
              disabled={busy}
              onClick={() => void find()}
            >
              {busy ? "Looking…" : "Find"}
            </button>
          </div>

          {lastSale ? (
            <button
              type="button"
              className={styles.quietWide}
              disabled={busy}
              onClick={() => void find(lastSale.id)}
            >
              The sale just now — #{lastSale.number}
            </button>
          ) : null}

          <div className={styles.actions}>
            <button type="button" className={styles.quiet} onClick={onClose}>
              Cancel
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── build the return, then have it approved ─────────────────────────────
  const methods = onAccount
    ? [...METHODS, { value: "credit", label: "On account" } as const]
    : METHODS;

  return (
    <div className={styles.wrap} role="dialog" aria-label="Take a return">
      <div className={styles.sheet}>
        <header className={styles.head}>
          <div>
            <p className={styles.eyebrow}>Return against sale</p>
            <h2 className={styles.title}>
              #{sale.number}
              <span className={styles.sold}>
                {" "}
                · {shillings(cents(sale.total))} sold
              </span>
            </h2>
          </div>
          <button
            type="button"
            className={styles.quiet}
            onClick={() => setSale(null)}
          >
            Different sale
          </button>
        </header>

        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}

        <div className={styles.tableWrap}>
          <table className={styles.lines}>
            <thead>
              <tr>
                <th scope="col">Line</th>
                <th scope="col" className={styles.num}>
                  Sold
                </th>
                <th scope="col" className={styles.num}>
                  Can return
                </th>
                <th scope="col" className={styles.num}>
                  Coming back
                </th>
                <th scope="col">Back on the shelf</th>
              </tr>
            </thead>
            <tbody>
              {sale.items.map((line) => {
                const remaining = left(line);
                const spent = remaining <= 0;
                return (
                  <tr key={line.id} className={spent ? styles.spent : undefined}>
                    <td>
                      <div className={styles.name}>{line.product_name}</div>
                      <div className={styles.sku}>{line.sku}</div>
                    </td>
                    <td className={styles.num}>{Number(line.quantity)}</td>
                    <td className={styles.num}>
                      {spent ? (
                        <span className={styles.spentWord}>all returned</span>
                      ) : (
                        remaining
                      )}
                    </td>
                    <td className={styles.num}>
                      <input
                        className={`${styles.qty} ${styles.mono}`}
                        inputMode="decimal"
                        disabled={spent}
                        value={quantities[line.id] ?? ""}
                        onChange={(e) =>
                          setQuantities((q) => ({
                            ...q,
                            [line.id]: e.target.value,
                          }))
                        }
                        placeholder="0"
                        aria-label={`How many ${line.product_name} are coming back`}
                      />
                    </td>
                    <td>
                      {/*
                        Ticked by default and untickable on purpose: a damaged
                        item counted back into stock is stock the shop does
                        not have. RefundItem.restocked carries the reasoning.
                      */}
                      <input
                        type="checkbox"
                        disabled={spent}
                        checked={restock[line.id] !== false}
                        onChange={(e) =>
                          setRestock((r) => ({
                            ...r,
                            [line.id]: e.target.checked,
                          }))
                        }
                        aria-label={`Put ${line.product_name} back into stock`}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <p className={styles.amount}>
          {shillings(preview)}{" "}
          <span className={styles.amountWord}>
            goes back {preview > 0 ? "" : "— nothing chosen yet"}
          </span>
        </p>

        <label className={styles.label} htmlFor="return-reason">
          Why
        </label>
        <input
          id="return-reason"
          className={styles.input}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Wrong size"
        />
        <span className={styles.hint}>
          Somebody will read this when they ask why the takings are down.
          Write what you would say out loud.
        </span>

        <span className={styles.label}>How the money goes back</span>
        <div className={styles.methods}>
          {methods.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`${styles.method} ${
                method === option.value ? styles.methodOn : ""
              }`}
              aria-pressed={method === option.value}
              onClick={() => setMethod(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className={styles.hint}>
          {method === "cash"
            ? "Out of this drawer. The close will expect that much less."
            : "Nothing leaves the drawer, so the close is unaffected."}
        </span>

        {/*
          ── THE SECOND PERSON ────────────────────────────────────────────
          Said plainly and in advance, rather than after a refusal. "Fetch
          your manager" is something a cashier can act on; a 403 at a
          counter is not. The same argument as the pay-out note in
          CashMovement.
        */}
        <div className={styles.gate}>
          <h3 className={styles.gateTitle}>A manager has to approve this</h3>
          <p className={styles.gateLede}>
            Giving money back is not something a till does on its own. Hand
            the terminal over — the sign-in below is theirs, is used once, and
            is not kept.
          </p>

          <label className={styles.label} htmlFor="return-manager">
            Manager&rsquo;s username
          </label>
          <input
            id="return-manager"
            className={styles.input}
            autoComplete="off"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />

          <label className={styles.label} htmlFor="return-password">
            Password
          </label>
          <input
            id="return-password"
            className={styles.input}
            type="password"
            autoComplete="off"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void authorise();
            }}
          />
        </div>

        <div className={styles.actions}>
          <button type="button" className={styles.quiet} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={busy}
            onClick={() => void authorise()}
          >
            {busy ? "Approving…" : "Approve the return"}
          </button>
        </div>
      </div>
    </div>
  );
}
