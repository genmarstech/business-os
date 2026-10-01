"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BusinessMark } from "@/components/BusinessMark";
import { Calculator } from "./Calculator";
import { ChangePassword } from "./ChangePassword";
import { ShiftNote } from "./ShiftNote";
import { SignIn } from "./SignIn";
import { Tender } from "./Tender";
import { cents, shillings, total, type Basket } from "./money";
import { call, forget, load, readError, save, type TillSession } from "./session";
import styles from "./till.module.css";

/**
 * The register.
 *
 * ── ONE SCREEN, THREE STATES, NO ROUTING ───────────────────────────────────
 * Signed out, no shift open, selling. A cashier does not navigate; they are
 * wherever the shift has got to, and a back button in the middle of a queue
 * is a way to lose a basket. The URL never changes while a sale is being
 * built.
 *
 * ── THE CATALOGUE IS FETCHED ONCE ──────────────────────────────────────────
 * Blueprint §6 asks for a register that keeps up with a queue. Searching in
 * memory is what makes typing feel instant, and it is also the shape an
 * offline till needs later. The cost is that a price changed mid-shift is not
 * seen until the catalogue is refreshed — so there is a visible Refresh, and
 * the server prices the sale anyway.
 */

type Product = {
  id: number;
  name: string;
  sku: string;
  barcode?: string;
  /**
   * The base. Shown struck through when a list has overridden it, and NEVER
   * charged off — see `price` below.
   */
  selling_price: string;
  /**
   * What it costs at THIS branch today, resolved by the server.
   *
   * ── DRAW FROM THIS, NOT FROM `selling_price` ────────────────────────────
   * A price list can override the base, and `catalog/pricing.py` is the one
   * place that decides by how much — the same module `checkout` calls. A
   * till that priced off `selling_price` would show the shelf price while
   * the server charged the promotion, which is a customer being charged
   * something other than what they were quoted.
   */
  price: string;
  price_list_name: string | null;
  is_active: boolean;
  tax_rule: number | null;
  category: { id: number; name: string } | number | null;
};

/** An outstanding request for a customer to approve a payment. */
type Push = {
  id: number;
  status: "requested" | "paid" | "failed" | "expired";
  status_label: string;
  amount: string;
  phone_number: string;
  mpesa_receipt: string;
  result_description: string;
};

type TaxRule = {
  id: number;
  rate: string;
  is_inclusive: boolean;
  is_active: boolean;
};

type Register = {
  id: number;
  name: string;
  register_number: string;
  branch: { id: number; branch_name: string } | null;
  is_active: boolean;
};

type Shift = {
  id: number;
  register: Register;
  operator: number;
  status: string;
  opened_at: string;
  note?: string;
};

type Sale = {
  id: number;
  number: string;
  total: string;
  tax_total: string;
  subtotal: string;
  items: { product_name: string; quantity: string; line_total: string }[];
  receipt?: { number?: string } | null;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T>): T[] {
  return Array.isArray(page) ? page : (page.results ?? []);
}

export function Till() {
  const [session, setSession] = useState<TillSession | null>(null);
  const [ready, setReady] = useState(false);
  const [deferred, setDeferred] = useState(false);

  // Read storage after mount, never during render: the server has no
  // localStorage, and reading it in render makes the first paint disagree
  // with the server's and throws a hydration error.
  useEffect(() => {
    setSession(load());
    setReady(true);
  }, []);

  if (!ready) return <div className={styles.boot}>Starting the till…</div>;
  if (!session) return <SignIn onSignedIn={setSession} />;

  /*
   * Before the till is chosen, and only here.
   *
   * This is the one moment a cashier is not mid-queue, which is why the ask
   * lives at sign-in rather than anywhere further in. Deferring is allowed —
   * see the banner on ChangePassword — and the gate note below keeps asking.
   *
   * `deferred` is component state and not storage on purpose: it lasts until
   * they sign out, so the next shift asks again. Remembering the refusal
   * would turn "later" into "never" silently.
   */
  if (session.must_change_password && !deferred) {
    return (
      <ChangePassword
        session={session}
        onDefer={() => setDeferred(true)}
        onChanged={() => {
          // The flag has to be cleared in the stored session as well as in
          // state: it is what the till reads on the next boot, and leaving it
          // set would ask somebody to change a password they just changed.
          const updated = { ...session, must_change_password: false };
          save(updated);
          setSession(updated);
        }}
      />
    );
  }

  return (
    <Shifted
      session={session}
      onSignOut={() => {
        forget();
        setSession(null);
      }}
    />
  );
}

/** Choosing a register and opening the drawer, then selling. */
function Shifted({
  session,
  onSignOut,
}: {
  session: TillSession;
  onSignOut: () => void;
}) {
  const [shift, setShift] = useState<Shift | null>(null);
  const [registers, setRegisters] = useState<Register[]>([]);
  const [opening, setOpening] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  /*
   * The registers this cashier may work, and whether one of them already has
   * their shift open.
   *
   * /org/staff/ is deliberately NOT called: it is held at staff.manage and a
   * cashier is refused it. The operator is the signed-in principal's own
   * staff id, which the sign-in response carries for exactly this reason.
   */
  const start = useCallback(async () => {
    setError("");
    try {
      const [registerPage, shiftPage] = await Promise.all([
        call<Page<Register>>("/brn/register/"),
        call<Page<Shift>>("/brn/register-shifts/"),
      ]);

      setRegisters(rows(registerPage).filter((r) => r.is_active));

      const open = rows(shiftPage).find(
        (s) => s.status === "OPEN" && s.operator === session.staff.id,
      );
      if (open) setShift(open);
    } catch (caught) {
      setError(readError(caught, "Could not reach the shop's records."));
    }
  }, [session.staff.id]);

  useEffect(() => {
    void start();
  }, [start]);

  async function open(registerId: number) {
    setBusy(true);
    setError("");
    try {
      const made = await call<Shift>("/brn/register-shifts/", {
        method: "POST",
        body: {
          register_id: registerId,
          operator: session.staff.id,
          opening_cash: opening || "0",
        },
      });
      setShift(made);
    } catch (caught) {
      setError(readError(caught, "The till would not open."));
    } finally {
      setBusy(false);
    }
  }

  if (shift) {
    return (
      <Selling session={session} shift={shift} onSignOut={onSignOut} />
    );
  }

  return (
    <div className={styles.gate}>
      <div className={styles.gateCard}>
        <h1 className={styles.gateTitle}>Which till?</h1>
        <p className={styles.gateLede}>
          Signed in as {session.staff.name} at {session.organisation.name}.
          Cash and sales are counted per till, which is what lets a drawer be
          reconciled at the end of your shift.
        </p>

        {session.must_change_password ? (
          <p className={styles.gateNote}>
            You are still using the password your manager set. Ask them to
            show you how to change it — until you do, anything rung up here is
            something they could also have done.
          </p>
        ) : null}

        {error ? (
          <p className={styles.gateError} role="alert">
            {error}
          </p>
        ) : null}

        <label className={styles.gateLabel} htmlFor="opening">
          Cash in the drawer now
        </label>
        <input
          id="opening"
          className={`${styles.gateInput} ${styles.mono}`}
          inputMode="decimal"
          value={opening}
          onChange={(e) => setOpening(e.target.value)}
        />
        <span className={styles.gateHint}>
          Count it before you start. It is what the close is measured against.
        </span>

        {registers.length === 0 ? (
          <p className={styles.gateHint}>
            No till is set up at your branch yet. Your manager adds one under
            Branches.
          </p>
        ) : (
          <div className={styles.registerList}>
            {registers.map((register) => (
              <button
                key={register.id}
                className={styles.registerButton}
                disabled={busy}
                onClick={() => open(register.id)}
              >
                <span className={styles.registerName}>{register.name}</span>
                <span className={styles.registerWhere}>
                  {register.branch?.branch_name ?? ""} ·{" "}
                  {register.register_number}
                </span>
              </button>
            ))}
          </div>
        )}

        <button className={styles.gateQuiet} onClick={onSignOut}>
          Sign out
        </button>
      </div>
    </div>
  );
}

/** The sale itself. */
function Selling({
  session,
  shift,
  onSignOut,
}: {
  session: TillSession;
  shift: Shift;
  onSignOut: () => void;
}) {
  const [products, setProducts] = useState<Product[]>([]);
  const [rules, setRules] = useState<Map<number, TaxRule>>(new Map());
  const [query, setQuery] = useState("");
  const [basket, setBasket] = useState<Basket[]>([]);
  const [method, setMethod] = useState("cash");
  const [tendered, setTendered] = useState("");

  /*
   * ── THE M-PESA PROMPT, WHILE THE CUSTOMER IS LOOKING AT THEIR PHONE ──────
   * `push` is the request we have out; null means we have not asked. The
   * sale is rung up only once it comes back paid — ringing it up first
   * would mean voiding one every time somebody changes their mind at the
   * PIN prompt, and a void is a thing a manager has to explain.
   */
  const [phone, setPhone] = useState("");
  const [push, setPush] = useState<Push | null>(null);
  const [sale, setSale] = useState<Sale | null>(null);
  const [tool, setTool] = useState<"calculator" | "note" | null>(null);
  const [note, setNote] = useState(shift.note ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const search = useRef<HTMLInputElement>(null);

  /*
   * ── THE IDEMPOTENCY KEY IS MADE BEFORE THE FIRST ATTEMPT, NOT AFTER ──────
   * Blueprint §11. It has to survive a retry, so it is minted when the basket
   * starts and reused for every attempt at the same sale — a key generated
   * per request would make a retry a second charge, which is the exact thing
   * the key exists to prevent.
   */
  const [key, setKey] = useState(() => crypto.randomUUID());

  // The branch this till stands in. Everything priced on this screen is
  // priced for here; a till cannot move between branches mid-shift.
  const branchId = shift.register?.branch?.id;

  const refresh = useCallback(async () => {
    setError("");
    try {
      /*
        `?branch=` is what lets the server apply this branch's own prices.
        Without it only organisation-wide lists can apply, because guessing
        at a branch's prices is how one shop's promotion gets shown at
        another.
      */
      const [productPage, rulePage] = await Promise.all([
        call<Page<Product>>(
          branchId ? `/ctl/products/?branch=${branchId}` : "/ctl/products/",
        ),
        call<Page<TaxRule>>("/sls/tax-rules/"),
      ]);
      setProducts(rows(productPage).filter((p) => p.is_active));
      setRules(new Map(rows(rulePage).map((r) => [r.id, r])));
    } catch (caught) {
      setError(readError(caught, "Could not load the catalogue."));
    }
  }, [branchId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return products.slice(0, 24);
    return products
      .filter(
        (p) =>
          p.barcode?.toLowerCase() === q ||
          p.sku.toLowerCase().includes(q) ||
          p.name.toLowerCase().includes(q),
      )
      .slice(0, 24);
  }, [products, query]);

  const add = useCallback(
    (product: Product) => {
      const rule = product.tax_rule ? rules.get(product.tax_rule) : undefined;
      setSale(null);
      setBasket((current) => {
        const at = current.findIndex((l) => l.productId === product.id);
        if (at >= 0) {
          const next = [...current];
          next[at] = { ...next[at]!, quantity: next[at]!.quantity + 1 };
          return next;
        }
        return [
          ...current,
          {
            productId: product.id,
            name: product.name,
            sku: product.sku,
            unitCents: cents(product.price),
            quantity: 1,
            taxRate: rule?.is_active ? Number(rule.rate) : 0,
            taxInclusive: rule?.is_inclusive ?? true,
          },
        ];
      });
      setQuery("");
      search.current?.focus();
    },
    [rules],
  );

  /*
   * A barcode scanner is a keyboard that types fast and presses Enter. An
   * exact barcode match on Enter adds it without the cashier touching
   * anything — which is the whole difference between scanning and typing.
   */
  function onSearchKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const q = query.trim().toLowerCase();
    const exact =
      products.find((p) => p.barcode && p.barcode.toLowerCase() === q) ??
      products.find((p) => p.sku.toLowerCase() === q);
    if (exact) add(exact);
    else if (matches.length === 1) add(matches[0]!);
  }

  function setQuantity(productId: number, quantity: number) {
    setSale(null);
    setBasket((current) =>
      quantity <= 0
        ? current.filter((l) => l.productId !== productId)
        : current.map((l) =>
            l.productId === productId ? { ...l, quantity } : l,
          ),
    );
  }

  const sums = total(basket);

  async function checkout(paidBy?: Push) {
    if (basket.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const done = await call<Sale>("/sls/sales/checkout/", {
        method: "POST",
        body: {
          shift: shift.id,
          // Pinned by the server to whoever is signed in, whatever is sent
          // here — see the attribution banner in sales/views.py. Sent anyway
          // because a subscriber posting the same shape may legitimately name
          // somebody else, so the field is not optional.
          cashier: session.staff.id,
          idempotency_key: key,
          lines: basket.map((l) => ({
            product: l.productId,
            quantity: String(l.quantity),
          })),
          payments: [
            {
              method,
              // The SERVER's total, not the preview's — except that the
              // server has not answered yet, so the preview is what is sent
              // and the server refuses the sale if it disagrees. Sending the
              // tendered amount instead would post a cash overage as revenue.
              amount: (sums.total / 100).toFixed(2),
              /*
                The confirmed push, so the server can spend it in the same
                transaction that writes the sale. Two requests would leave a
                window where the sale exists and the payment is still
                unspent — and an unspent confirmed payment can fund somebody
                else's basket.
              */
              ...(paidBy
                ? { stk_push: paidBy.id, reference: paidBy.mpesa_receipt }
                : {}),
            },
          ],
        },
      });
      setSale(done);
      setBasket([]);
      setTendered("");
      setPhone("");
      setPush(null);
      setKey(crypto.randomUUID());
      search.current?.focus();
    } catch (caught) {
      setError(readError(caught, "The sale did not go through."));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Ask the customer's phone to approve the payment.
   *
   * The amount is the preview's, as it is for cash — the server prices the
   * sale itself and refuses one whose total disagrees.
   */
  async function askForPayment() {
    if (basket.length === 0 || !branchId) return;
    setBusy(true);
    setError("");
    try {
      const started = await call<Push>("/pay/mpesa/pushes/request/", {
        method: "POST",
        body: {
          branch: branchId,
          amount: (sums.total / 100).toFixed(2),
          phone_number: phone,
          description: "Purchase",
        },
      });
      setPush(started);
    } catch (caught) {
      setError(readError(caught, "Could not reach M-Pesa."));
    } finally {
      setBusy(false);
    }
  }

  /*
   * ── THE TILL ASKS; IT DOES NOT WAIT TO BE TOLD ──────────────────────────
   *
   * Safaricom post the result to a callback, and that callback gets lost
   * often enough to design for — a dropped connection, a proxy, a bad
   * minute at their end. A till that could only learn the answer from the
   * callback would hang with a customer standing there.
   *
   * So this polls, and the endpoint it polls asks Safaricom directly every
   * time. The callback only makes the answer arrive sooner.
   */
  useEffect(() => {
    if (!push || push.status !== "requested") return;

    let live = true;
    const timer = setInterval(async () => {
      try {
        const latest = await call<Push>(`/pay/mpesa/pushes/${push.id}/check/`);
        if (!live) return;
        setPush(latest);
      } catch {
        /*
          A failed poll is not a failed payment, and saying so would send a
          cashier to take cash from somebody who has already paid. Stay
          quiet and ask again in three seconds.
        */
      }
    }, 3000);

    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [push]);

  /*
   * Paid: ring the sale up. Separate from the poll so it runs once, on the
   * transition, rather than on every tick that happens to see "paid".
   */
  useEffect(() => {
    if (push?.status === "paid" && basket.length > 0 && !busy) {
      void checkout(push);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [push?.status]);

  /*
   * ── THERE IS NO "CLOSE TILL" BUTTON, AND THERE SHOULD NOT BE ────────────
   *
   * There was one. It PATCHed the shift's status, which the server no longer
   * accepts from anybody — a shift ends by being counted, and SHIFT_CLOSE is
   * a manager's permission. So the button offered a cashier something that
   * would always be refused, and its error message said so after the fact.
   *
   * What a cashier needs at the end of a shift is to have written the note
   * and to fetch their manager. Both are in the bar.
   */

  return (
    <div className={styles.till}>
      <header className={styles.bar}>
        <div className={styles.barBrand}>
          <BusinessMark size={22} />
          <div>
            <div className={styles.barShop}>{session.organisation.name}</div>
            <div className={styles.barWhere}>
              {shift.register?.name ?? "Till"} ·{" "}
              {shift.register?.branch?.branch_name ?? ""}
            </div>
          </div>
        </div>
        <div className={styles.barRight}>
          <span className={styles.barWho}>{session.staff.name}</span>

          {/*
            ── THE TOOLS A COUNTER ALREADY HAS, BROUGHT INSIDE ──────────────
            A cashier reaching for their phone to split a bill has stopped
            serving and taken their eyes off the drawer. Both of these sit in
            the bar rather than the sale flow: they are needed occasionally
            and must never be in the way of the next customer.
          */}
          <button
            className={styles.barQuiet}
            onClick={() => setTool(tool === "calculator" ? null : "calculator")}
            aria-expanded={tool === "calculator"}
          >
            Calculator
          </button>
          <button
            className={styles.barQuiet}
            onClick={() => setTool(tool === "note" ? null : "note")}
            aria-expanded={tool === "note"}
          >
            {note.trim() ? "Note ✓" : "Note"}
          </button>

          <button className={styles.barQuiet} onClick={() => void refresh()}>
            Refresh prices
          </button>
          <button className={styles.barQuiet} onClick={onSignOut}>
            Sign out
          </button>
        </div>
      </header>

      {tool === "calculator" ? (
        <Calculator onClose={() => setTool(null)} />
      ) : null}
      {tool === "note" ? (
        <ShiftNote
          shiftId={shift.id}
          note={note}
          onSaved={setNote}
          onClose={() => setTool(null)}
        />
      ) : null}

      <div className={styles.floor}>
        <section className={styles.picker}>
          <input
            ref={search}
            className={styles.search}
            placeholder="Scan a barcode, or type a name or code"
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onSearchKey}
          />

          {products.length === 0 ? (
            <p className={styles.note}>
              Nothing is listed for sale yet. A manager adds products under
              Catalogue.
            </p>
          ) : (
            <div className={styles.grid}>
              {matches.map((product) => (
                <button
                  key={product.id}
                  className={styles.tile}
                  onClick={() => add(product)}
                >
                  <span className={styles.tileName}>{product.name}</span>
                  <span className={styles.tilePrice}>
                    {shillings(cents(product.price))}
                    {/*
                      The base, struck through, when a list has moved it.
                      A cashier asked "why is this 80?" can answer without
                      leaving the till — and sees at a glance that the
                      promotion is actually running, which is the thing
                      that goes wrong on the morning one was supposed to
                      start.
                    */}
                    {product.price !== product.selling_price ? (
                      <span className={styles.tileWas}>
                        {shillings(cents(product.selling_price))}
                      </span>
                    ) : null}
                  </span>
                </button>
              ))}
              {matches.length === 0 ? (
                <p className={styles.note}>Nothing matches “{query}”.</p>
              ) : null}
            </div>
          )}
        </section>

        <aside className={styles.basket}>
          {sale ? (
            <Receipt sale={sale} onNext={() => setSale(null)} />
          ) : (
            <>
              <div className={styles.lines}>
                {basket.length === 0 ? (
                  <p className={styles.note}>
                    Nothing in the basket. Scan or tap to start.
                  </p>
                ) : (
                  basket.map((line) => (
                    <div key={line.productId} className={styles.line}>
                      <div>
                        <div className={styles.lineName}>{line.name}</div>
                        <div className={styles.lineMeta}>
                          {shillings(line.unitCents)} each
                        </div>
                      </div>
                      <div className={styles.qty}>
                        <button
                          aria-label={`One fewer ${line.name}`}
                          onClick={() =>
                            setQuantity(line.productId, line.quantity - 1)
                          }
                        >
                          −
                        </button>
                        <span>{line.quantity}</span>
                        <button
                          aria-label={`One more ${line.name}`}
                          onClick={() =>
                            setQuantity(line.productId, line.quantity + 1)
                          }
                        >
                          +
                        </button>
                      </div>
                      <div className={styles.lineTotal}>
                        {shillings(line.unitCents * line.quantity)}
                      </div>
                    </div>
                  ))
                )}
              </div>

              <div className={styles.sums}>
                <div>
                  <span>Subtotal</span>
                  <span>{shillings(sums.subtotal)}</span>
                </div>
                <div>
                  <span>
                    Tax
                    {sums.taxIncluded === sums.tax && sums.tax > 0
                      ? " (in the prices)"
                      : ""}
                  </span>
                  <span>{shillings(sums.tax)}</span>
                </div>
                <div className={styles.grand}>
                  <span>Total</span>
                  <span>{shillings(sums.total)}</span>
                </div>
              </div>

              {error ? (
                <p className={styles.error} role="alert">
                  {error}
                </p>
              ) : null}

              <div className={styles.methods}>
                {[
                  ["cash", "Cash"],
                  ["mpesa", "M-Pesa"],
                  ["card", "Card"],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    className={`${styles.method} ${
                      method === value ? styles.methodOn : ""
                    }`}
                    onClick={() => setMethod(value!)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/*
                Only cash needs a tendered figure. M-Pesa and card arrive for
                the exact amount, and asking "how much did they give you" for
                a card payment is a question with no meaning that somebody
                would eventually answer wrongly.
              */}
              {method === "cash" ? (
                <Tender
                  totalCents={sums.total}
                  tendered={tendered}
                  onChange={setTendered}
                />
              ) : null}

              {method === "mpesa" ? (
                <Mpesa
                  totalCents={sums.total}
                  phone={phone}
                  onPhone={setPhone}
                  push={push}
                  busy={busy}
                  onAsk={() => void askForPayment()}
                  onGiveUp={() => setPush(null)}
                />
              ) : null}

              {/*
                Hidden while an M-Pesa prompt is out. Leaving it there
                invites a cashier to press it, which would ring the sale up
                as if the money had arrived — the one mistake this whole
                flow exists to make impossible.
              */}
              {method === "mpesa" ? null : (
                <button
                  className={styles.take}
                  disabled={busy || basket.length === 0}
                  onClick={() => void checkout()}
                >
                  {busy ? "Taking…" : `Take ${shillings(sums.total)}`}
                </button>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

/**
 * Asking for an M-Pesa payment, and waiting for it.
 *
 * ── THE WAIT IS THE SCREEN, NOT A SPINNER ─────────────────────────────────
 * A customer takes twenty or thirty seconds to find their phone and type a
 * PIN, and for all of that time the cashier needs to know three things: that
 * something was sent, to which number, and that they can still fall back to
 * cash. A spinner says none of them.
 *
 * ── AND NOTHING HERE MARKS A SALE PAID ────────────────────────────────────
 * There is no "confirm" button, deliberately. A cashier who could say the
 * money arrived is a cashier who can be talked into saying it by a customer
 * holding a convincing SMS. Only Safaricom's own answer moves this on.
 */
function Mpesa({
  totalCents,
  phone,
  onPhone,
  push,
  busy,
  onAsk,
  onGiveUp,
}: {
  totalCents: number;
  phone: string;
  onPhone: (value: string) => void;
  push: Push | null;
  busy: boolean;
  onAsk: () => void;
  onGiveUp: () => void;
}) {
  const waiting = push?.status === "requested";
  const settledBadly =
    push && (push.status === "failed" || push.status === "expired");

  if (waiting) {
    return (
      <div className={styles.mpesaWaiting} role="status">
        <div className={styles.mpesaHeading}>
          Waiting for {push.phone_number}
        </div>
        <p className={styles.mpesaNote}>
          They should see a prompt for {shillings(totalCents)} on their phone.
          The sale rings itself up the moment M-Pesa confirms it.
        </p>
        <button className={styles.mpesaQuiet} onClick={onGiveUp}>
          Give up and take cash
        </button>
      </div>
    );
  }

  return (
    <div className={styles.mpesaAsk}>
      {settledBadly ? (
        <p className={styles.mpesaFailed} role="alert">
          {push.result_description || push.status_label}. Try again, or take
          cash.
        </p>
      ) : null}
      <label className={styles.mpesaLabel} htmlFor="mpesa-phone">
        Customer&rsquo;s phone
      </label>
      <input
        id="mpesa-phone"
        className={styles.mpesaPhone}
        inputMode="tel"
        autoComplete="off"
        placeholder="0712 345 678"
        value={phone}
        onChange={(e) => onPhone(e.target.value)}
      />
      <button
        className={styles.take}
        disabled={busy || phone.trim().length < 9}
        onClick={onAsk}
      >
        {busy ? "Asking…" : `Request ${shillings(totalCents)}`}
      </button>
    </div>
  );
}

/**
 * What was actually charged.
 *
 * Every figure here comes from the server's response, never from the preview
 * — see the banner on money.ts. This is the receipt.
 */
function Receipt({ sale, onNext }: { sale: Sale; onNext: () => void }) {
  return (
    <div className={styles.receipt}>
      <div className={styles.receiptHead}>
        <div className={styles.receiptTitle}>Paid</div>
        <div className={styles.receiptNumber}>{sale.number}</div>
      </div>

      <div className={styles.lines}>
        {sale.items.map((item, index) => (
          <div key={index} className={styles.line}>
            <div className={styles.lineName}>{item.product_name}</div>
            <div className={styles.lineMeta}>× {Number(item.quantity)}</div>
            <div className={styles.lineTotal}>
              {shillings(cents(item.line_total))}
            </div>
          </div>
        ))}
      </div>

      <div className={styles.sums}>
        <div>
          <span>Subtotal</span>
          <span>{shillings(cents(sale.subtotal))}</span>
        </div>
        <div>
          <span>Tax</span>
          <span>{shillings(cents(sale.tax_total))}</span>
        </div>
        <div className={styles.grand}>
          <span>Total</span>
          <span>{shillings(cents(sale.total))}</span>
        </div>
      </div>

      <button className={styles.take} onClick={onNext} autoFocus>
        Next customer
      </button>
    </div>
  );
}
