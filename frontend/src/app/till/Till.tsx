"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { BusinessMark } from "@/components/BusinessMark";
import { Calculator } from "./Calculator";
import { CashMovement } from "./CashMovement";
import { Count } from "./Count";
import { Return } from "./Return";
import { Scanner } from "./Scanner";
import { scanning } from "./barcode";
import { ChangePassword } from "./ChangePassword";
import { Reconcile } from "./Reconcile";
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
  discount_total: string;
  completed_at: string | null;
  items: {
    product_name: string;
    sku: string;
    quantity: string;
    unit_price: string;
    /*
     * What it would have cost without the promotion, and what the customer
     * was spared. Both "0.00" when no price list undercut the usual price,
     * which is most lines — the slip prints nothing in that case rather
     * than a saving of nothing.
     */
    usual_price: string;
    saved: string;
    offer_name: string;
    discount_amount: string;
    tax_amount: string;
    line_total: string;
  }[];
  /*
   * ── THE NAMES COME FROM THE SALE, NOT FROM THIS TERMINAL ───────────────
   * A receipt reprinted tomorrow by somebody else must still say who rang
   * it up today. Filling these from the till's own session would put the
   * current cashier's name on an old sale, which is exactly the signature
   * a duplicate-receipt fraud is spotted by.
   */
  organisation_name: string;
  branch_name: string;
  branch_location: string;
  register_name: string;
  cashier_name: string;
  customer_name: string;
  customer_phone: string;
  payments: {
    method: string;
    method_label: string;
    amount: string;
    reference: string;
    tendered: string | null;
    change_given: string | null;
  }[];
  receipt?: { number?: string; issued_at?: string } | null;
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
    <Signed
      session={session}
      onSignOut={() => {
        forget();
        setSession(null);
      }}
    />
  );
}

/**
 * Sending somebody to their work.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE SERVER SAYS WHERE. THIS SCREEN USED TO GUESS, AND GUESSED WRONG.
 *
 * It read `sales.checkout` and `inventory.count` off /auth/me and chose
 * between two screens. That was right for the two roles it was written for
 * and silently broken for the rest: a purchasing officer, a finance clerk and
 * a branch auditor hold neither, so the second condition sent them to the
 * register chooser, which asks for registers and shifts — both held at
 * `sales.view`, which a purchasing officer also lacks. The 403 came back as
 * "Could not reach the shop's records", which reads as a broken network and
 * never stopped being true.
 *
 * `workspaces` is identity/access.py's answer, ordered, and it arrives both
 * with the sign-in and from /auth/me. One entry means go; several mean ask;
 * none means we have not built their screen, which is said plainly instead of
 * being rendered as a fault in the shop's wifi.
 * ══════════════════════════════════════════════════════════════════════════
 */
function Signed({
  session,
  onSignOut,
}: {
  session: TillSession;
  onSignOut: () => void;
}) {
  /*
   * ── TWO DIFFERENT QUESTIONS, BOTH ANSWERED BY /auth/me ──────────────────
   *
   *   workspaces   WHERE to send them. Ordered, and the server's answer.
   *   permissions  what to draw once they are there — `Count` reads
   *                `inventory.count.close` off it, because counting a shelf
   *                and signing off what the count found are deliberately
   *                different authorities.
   *
   * Asked on boot even though the stored session carries `workspaces`,
   * because the permission list is not stored and must not be: an assignment
   * ended mid-shift has to take effect on the next screen, not at the next
   * sign-in.
   */
  const [me, setMe] = useState<{
    workspaces: string[];
    permissions: string[];
  } | null>(null);
  const [doing, setDoing] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    /*
     * A failure falls back to what the sign-in stored, and to selling when
     * there is nothing stored either — which is what the till did before any
     * of this existed. A stock count can wait for the network; a queue
     * cannot, and every endpoint checks again anyway, so the worst case is a
     * register that refuses somebody rather than a till that will not start.
     *
     * Permissions fall back to NONE, not to the stored list. Fail-closed on
     * what somebody may DO, fail-open on where they are sent.
     */
    const stored = session.workspaces?.length ? session.workspaces : ["sell"];

    call<{ workspaces?: string[]; permissions?: string[] }>("/auth/me")
      .then(
        (answer) =>
          live &&
          setMe({
            workspaces: answer.workspaces ?? stored,
            permissions: answer.permissions ?? [],
          }),
      )
      .catch(() => live && setMe({ workspaces: stored, permissions: [] }));
    return () => {
      live = false;
    };
  }, [session.workspaces]);

  if (me === null) {
    return <div className={styles.boot}>Starting the till…</div>;
  }

  const places = me.workspaces;
  const going = doing ?? (places.length === 1 ? places[0] : null);

  /*
   * ⚠ NO SCREEN YET, AND NOT A REFUSAL.
   *
   * Their login works and the API answers them — what is missing is a till
   * workspace for their role. Saying so, and naming the roles, is the
   * difference between an employee telling their manager "my login is
   * broken" and the manager knowing to put them on something else. Charter
   * 04 §IV: nothing untrue on a Genmars surface, and "sign-in failed" would
   * be untrue.
   */
  if (places.length === 0) {
    return (
      <div className={styles.gate}>
        <div className={styles.gateCard}>
          <h1 className={styles.gateTitle}>Your work is in the office</h1>
          <p className={styles.gateLede}>
            Your login works, {session.staff.name} — it is just not a till
            login. Your role does not ring up sales or count stock; buying,
            figures and audit are done on the office screens.
          </p>
          <p className={styles.gateNote}>
            Sign in at <strong>/sign-in</strong> with the same business
            number, username and password you used here. Nothing else to set
            up — it is the same login.
          </p>
          <button
            type="button"
            className={styles.gateButton}
            onClick={onSignOut}
          >
            Sign out
          </button>
        </div>
      </div>
    );
  }

  if (going === "count") {
    return (
      <Count
        permissions={me.permissions}
        onLeave={places.length > 1 ? () => setDoing(null) : onSignOut}
      />
    );
  }

  if (going === "sell") {
    return (
      <Shifted
        session={session}
        permissions={me.permissions}
        onSignOut={onSignOut}
      />
    );
  }

  return (
    <div className={styles.choose}>
      <p className={styles.chooseName}>{session.staff.name}</p>
      <h1 className={styles.chooseTitle}>What are you here to do?</h1>
      <div className={styles.chooseRow}>
        {places.includes("sell") ? (
          <button type="button" className={styles.chooseOne} onClick={() => setDoing("sell")}>
            <strong>Sell</strong>
            <span>Open a register and serve customers</span>
          </button>
        ) : null}
        {places.includes("count") ? (
          <button type="button" className={styles.chooseOne} onClick={() => setDoing("count")}>
            <strong>Count stock</strong>
            <span>Walk the shelves and record what is there</span>
          </button>
        ) : null}
      </div>
      <button type="button" className={styles.chooseOut} onClick={onSignOut}>
        Sign out
      </button>
    </div>
  );
}

/** Choosing a register and opening the drawer, then selling. */
function Shifted({
  session,
  permissions,
  onSignOut,
}: {
  session: TillSession;
  /**
   * Threaded down from `Signed` rather than re-fetched here.
   *
   * `Selling` needs it to know whether to offer taking cash OUT of the
   * drawer, which needs `shift.close`. A second /auth/me call would be a
   * second answer that can disagree with the first — and the one that
   * decides what a cashier may do should not be the stale one.
   */
  permissions: string[];
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
      <Selling
        session={session}
        shift={shift}
        permissions={permissions}
        onSignOut={onSignOut}
      />
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
  permissions,
  onSignOut,
}: {
  session: TillSession;
  shift: Shift;
  /** Only to decide whether to offer taking cash OUT. Not security — the
      server refuses a pay-out without `shift.close` whatever this says. */
  permissions: string[];
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
  const [tool, setTool] = useState<
    "calculator" | "note" | "cash" | "return" | "reconcile" | null
  >(null);
  const [camera, setCamera] = useState(false);
  /*
   * Asked once, after mount. `scanning()` reads `window`, so calling it
   * during render would disagree with the server's render and throw a
   * hydration error — the same reason the stored session is read in an
   * effect rather than inline.
   */
  const [canScan, setCanScan] = useState(false);
  useEffect(() => setCanScan(scanning()), []);
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

  /**
   * Resolve scanned digits to a product, or decide nothing matched.
   *
   * ── THE BARCODE IS MATCHED EXACTLY; THE SKU IS THE FALLBACK ─────────────
   * A barcode either is the product's or is not — a partial match on thirteen
   * digits is a different product, and adding the wrong line to a basket is
   * money. The SKU is tried second because a shop that prints its own labels
   * for loose goods prints the SKU on them, and those labels are Code 128
   * rather than EAN.
   *
   * Shared by the camera and by the wedge scanner below, so the two cannot
   * resolve a code differently — which they would, eventually, as one of them
   * gained a case the other did not.
   */
  const resolve = useCallback(
    (code: string): Product | undefined => {
      const q = code.trim().toLowerCase();
      if (!q) return undefined;
      return (
        products.find((p) => p.barcode && p.barcode.toLowerCase() === q) ??
        products.find((p) => p.sku.toLowerCase() === q)
      );
    },
    [products],
  );

  /** What the camera calls. False means "nothing matched", and it says so. */
  const scanned = useCallback(
    (code: string): boolean => {
      const found = resolve(code);
      if (!found) return false;
      add(found);
      return true;
    },
    [resolve, add],
  );

  /*
   * A wedge scanner is a keyboard that types fast and presses Enter — the
   * cheap USB kind on a counter, and it needs no camera and no permission.
   * An exact match on Enter adds it without the cashier touching anything,
   * which is the whole difference between scanning and typing.
   */
  function onSearchKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const exact = resolve(query);
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
      /*
       * ══════════════════════════════════════════════════════════════════
       * WHAT THIS SAYS WHEN THE CUSTOMER HAS ALREADY PAID.
       *
       * ⚠ "The sale did not go through." IS A LIE ONCE `paidBy` EXISTS,
       *   AND IT IS THE EXPENSIVE KIND.
       *
       * It happened in production: a confirmed M-Pesa push, the checkout
       * request never reached the server, and the till told the cashier
       * the sale had failed while the customer's phone was showing an
       * M-Pesa SMS for the same amount. From there a cashier either
       * presses M-Pesa again — charging a second time, because the button
       * sends a NEW push — or waves the customer through and the shop
       * takes the loss. Both are worse than the original fault.
       *
       * So the money and the paperwork are reported separately. The push
       * is already confirmed and already spendable; only the sale is
       * missing, and `onFinish` below writes it from the SAME push and
       * the same idempotency key.
       * ══════════════════════════════════════════════════════════════════
       */
      setError(
        paidBy
          ? readError(
              caught,
              "The money arrived, but the sale was not written. " +
                "Press “Finish the sale” — the customer will not be charged again.",
            )
          : readError(caught, "The sale did not go through."),
      );
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
    /*
     * ── NEVER TWO PUSHES FOR ONE BASKET ────────────────────────────────
     * A paid push that has not been spent yet is money already taken. The
     * button that reaches here says "Request", and pressing it again in
     * that state sends a SECOND prompt for the same shopping — which is
     * exactly what a cashier does when the till has just told them the
     * sale failed. The UI hides the button in that state; this is the
     * guard that does not depend on the UI being right.
     */
    if (push?.status === "paid") return;
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
  /*
   * ══════════════════════════════════════════════════════════════════════
   * ONE AUTOMATIC ATTEMPT PER PUSH. THE REF IS THE WHOLE POINT.
   *
   * This has now been wrong in both directions, and the ref is what makes
   * it neither.
   *
   *   Deps `[push?.status]` with a `!busy` guard — a terminal that was
   *   busy at the instant the status became "paid" refused once and never
   *   ran again, because the status stays "paid". A confirmed payment and
   *   a basket, with nothing waiting to happen.
   *
   *   Deps `[push?.status, busy, sale]` — checkout sets `busy` true then
   *   false, the effect re-runs on the way back down, and if the sale is
   *   refused it tries again. Production turned that into a POST per
   *   second against a checkout that could not succeed: the button
   *   flickered between "Finish the sale" and "Finishing…", the error
   *   flashed on and off, and the till had to be reloaded.
   *
   * So the ATTEMPT is recorded, not inferred from state. One automatic
   * try per push id; after that the cashier is holding a button and the
   * till is still.
   *
   * ⚠ A refusal here is usually not transient. The one that caused the
   *   loop was a basket that cannot be sold at this branch at all, and
   *   retrying that until the heat death of the universe helps nobody.
   */
  const triedFor = useRef<number | null>(null);

  useEffect(() => {
    if (push?.status !== "paid" || basket.length === 0 || busy || sale) return;
    if (triedFor.current === push.id) return;
    triedFor.current = push.id;
    void checkout(push);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [push?.status, push?.id, busy, sale, basket.length]);

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
          {/*
            Beside the note rather than in the sale flow, for the same reason:
            needed a few times a shift and never in the way of the next
            customer. It is what stops the drawer reading short on any day
            somebody banks the takings — see CashMovement.
          */}
          <button
            className={styles.barQuiet}
            onClick={() => setTool(tool === "cash" ? null : "cash")}
            aria-expanded={tool === "cash"}
          >
            Cash in/out
          </button>
          {/*
            ── IN THE BAR, AND OFFERED TO EVERY CASHIER ───────────────────
            Not hidden behind `sales.refund`, which no cashier holds. The
            cashier is the person a customer hands a receipt to, and they do
            all of the work on this screen — finding the sale, counting what
            came back, saying where it goes. A manager signs the one request
            that gives money back.

            Hiding it would leave the cashier with nothing to do but send
            the customer away to find somebody, which is the behaviour this
            replaces. Same reasoning as the pay-out option in CashMovement:
            the thing a cashier needs to know is that a manager can do it.
          */}
          <button
            className={styles.barQuiet}
            onClick={() => setTool(tool === "return" ? null : "return")}
            aria-expanded={tool === "return"}
          >
            Return
          </button>

          {/*
            ── WHY THIS IS IN THE BAR AND NOT IN THE OFFICE ───────────────
            "Did that M-Pesa go through?" is asked at the counter, with the
            customer standing there, about a payment the till screen has
            already moved on from. Answering it by ringing a manager is how
            a queue stops. Every button inside it only ASKS Safaricom — see
            the banner in Reconcile.
          */}
          <button
            className={styles.barQuiet}
            onClick={() => setTool(tool === "reconcile" ? null : "reconcile")}
            aria-expanded={tool === "reconcile"}
          >
            Check a payment
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
      {tool === "reconcile" ? (
        <Reconcile
          branchId={shift.register?.branch?.id ?? null}
          onClose={() => setTool(null)}
        />
      ) : null}
      {tool === "note" ? (
        <ShiftNote
          shiftId={shift.id}
          note={note}
          onSaved={setNote}
          onClose={() => setTool(null)}
        />
      ) : null}
      {tool === "cash" ? (
        <CashMovement
          shiftId={shift.id}
          // Drawn from the permission list, and not security: the server
          // refuses a pay-out without shift.close whatever this says. It only
          // lets the screen explain in advance rather than after a refusal.
          maySendOut={permissions.includes("shift.close")}
          onRecorded={() => setTool(null)}
          onClose={() => setTool(null)}
        />
      ) : null}
      {tool === "return" ? (
        <Return
          session={session}
          shiftId={shift.id}
          branchId={shift.register?.branch?.id ?? null}
          // The sale rung up a moment ago, so "I've changed my mind" is one
          // press rather than a receipt number typed back in. Null once the
          // basket moves on, which is correct — by then the receipt is the
          // only thing that identifies it.
          lastSale={sale ? { id: sale.id, number: sale.number } : null}
          onClose={() => setTool(null)}
        />
      ) : null}

      {camera ? (
        <Scanner
          title="Scan to add"
          hint="Point at the barcode. Keep going for the whole basket."
          // The camera stays open: a basket is six things, and closing
          // between each would make scanning slower than typing.
          continuous
          onScan={scanned}
          onClose={() => {
            setCamera(false);
            search.current?.focus();
          }}
        />
      ) : null}

      <div className={styles.floor}>
        <section className={styles.picker}>
          <div className={styles.searchRow}>
            <input
              ref={search}
              className={styles.search}
              placeholder="Scan a barcode, or type a name or code"
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onSearchKey}
            />
            {/*
              Only where the browser can actually do it — see the banner on
              barcode.ts. Absent rather than disabled: a permanently dead
              button on an iPhone invites somebody to keep pressing it, and
              the input beside it already says a barcode can be scanned,
              which remains true with a wedge scanner on every device.
            */}
            {canScan ? (
              <button
                type="button"
                className={styles.scan}
                onClick={() => setCamera(true)}
              >
                Scan
              </button>
            ) : null}
          </div>

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
                  /*
                    The same push and the same idempotency key as the
                    attempt that failed, so the server either writes the
                    sale or hands back the one it already wrote. Nothing
                    about this reaches Safaricom.
                  */
                  onFinish={() => push && void checkout(push)}
                  /*
                    Clears the till without touching the payment. The push
                    stays paid and unspent on the server, which is exactly
                    what happened and what the office needs to see.
                  */
                  onSetAside={() => {
                    setPush(null);
                    setError("");
                    setPhone("");
                  }}
                  failure={error}
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
  onFinish,
  onSetAside,
  failure,
}: {
  totalCents: number;
  phone: string;
  onPhone: (value: string) => void;
  push: Push | null;
  busy: boolean;
  onAsk: () => void;
  onGiveUp: () => void;
  /** Write the sale for a push that is already paid. Never a new charge. */
  onFinish: () => void;
  /** Release the till, leaving the payment confirmed and unspent. */
  onSetAside: () => void;
  /** Why finishing was refused, if it was. */
  failure: string;
}) {
  const waiting = push?.status === "requested";
  const settledBadly =
    push && (push.status === "failed" || push.status === "expired");
  /*
   * Paid, and the basket is still here — so the sale was not written. The
   * money is in the shop's account either way; what is missing is the
   * paperwork, and the only safe action is to finish it with THIS push.
   */
  const paidButUnfinished = push?.status === "paid";

  if (paidButUnfinished) {
    return (
      <div className={styles.mpesaWaiting} role="status">
        <div className={styles.mpesaHeading}>
          {shillings(totalCents)} received from {push.phone_number}
        </div>
        <p className={styles.mpesaNote}>
          M-Pesa has confirmed the payment. The sale still has to be written
          — this finishes it with the payment already made, and does not
          charge the customer again.
        </p>
        {/*
          The refusal, repeated here. It is also shown above the payment
          methods, but this panel is what the cashier is looking at, and a
          reason they have to go and find is a reason they do not read.
        */}
        {failure ? (
          <p className={styles.mpesaFailed} role="alert">
            {failure}
          </p>
        ) : null}
        <button
          className={styles.take}
          onClick={onFinish}
          disabled={busy}
          autoFocus
        >
          {busy ? "Finishing…" : "Finish the sale"}
        </button>
        {/*
          ── THERE HAS TO BE A WAY OUT, AND IT IS NOT "TAKE CASH" ────────
          This panel had one button. When finishing kept failing — a
          basket that cannot be sold at this branch at all — the cashier
          was looking at a button that would never work, with no way back
          to the basket, and reloaded the till to escape. A screen with no
          exit is a screen somebody escapes destructively.

          It is deliberately NOT "give up and take cash": the money has
          arrived, and taking cash as well charges twice. What this does
          is release the till. The payment stays on record, confirmed and
          unspent, where the office can see it and attach it to a sale —
          which is the honest state of affairs when the shop has been paid
          for something it could not sell.
        */}
        <button className={styles.mpesaQuiet} onClick={onSetAside}>
          Set aside and carry on — the payment stays on record
        </button>
      </div>
    );
  }

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
/** Nairobi, always. A receipt is read where it was printed. */
function stamp(when: string | null | undefined): string {
  if (!when) return "";
  return new Date(when).toLocaleString("en-GB", {
    timeZone: "Africa/Nairobi",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/*
 * ── DRIVING A THERMAL PRINTER FROM A BROWSER ──────────────────────────────
 *
 * There is no direct ESC/POS here and there should not be: a browser cannot
 * open a USB or serial device, and the alternative is a local print agent —
 * a second thing to install, run and keep alive on every terminal in every
 * shop. The receipt printer is installed as the terminal's system printer
 * instead, and `window.print()` reaches it through the operating system's
 * own driver. globals.css sizes the slip to 72mm and forces black on white
 * so what comes out is a till roll and not a screenshot of the register.
 *
 * ── WHY AUTO-PRINT IS REMEMBERED PER TERMINAL ────────────────────────────
 * A shop with a printer wants one on every sale and never wants to press
 * anything; a shop without one wants no dialogue at all. That is a property
 * of the TERMINAL, not of the business and not of the cashier — the same
 * shop can have a printer on till 1 and none on the phone a supervisor
 * carries. So it lives in this browser's storage beside the till session,
 * wrapped because a kiosk browser can have storage disabled.
 */
const AUTO_PRINT = "till.autoprint";

function autoPrintWanted(): boolean {
  try {
    return window.localStorage.getItem(AUTO_PRINT) === "yes";
  } catch {
    return false;
  }
}

function rememberAutoPrint(on: boolean): void {
  try {
    window.localStorage.setItem(AUTO_PRINT, on ? "yes" : "no");
  } catch {
    /* A till with storage off simply asks every time. */
  }
}

function Receipt({ sale, onNext }: { sale: Sale; onNext: () => void }) {
  const discount = cents(sale.discount_total || "0");
  /*
   * ── WHAT THE OFFERS CAME TO ───────────────────────────────────────────
   * Summed from the lines rather than carried on the sale: the lines are
   * the record, and a total that can drift from them is a receipt arguing
   * with itself in front of a customer.
   */
  const saved = sale.items.reduce((sum, item) => sum + cents(item.saved), 0);
  const issued = sale.receipt?.issued_at ?? sale.completed_at;
  const [auto, setAuto] = useState(autoPrintWanted);

  /*
   * Once, when the slip first appears, and only for this sale — `sale.id`
   * rather than `auto`, so ticking the box mid-receipt does not fire a
   * print for the one already on screen. The timeout lets the browser lay
   * the slip out before it is captured; printing in the same tick can
   * produce a blank page.
   */
  useEffect(() => {
    if (!autoPrintWanted()) return;
    const timer = setTimeout(() => window.print(), 150);
    return () => clearTimeout(timer);
  }, [sale.id]);

  return (
    <div className={styles.receipt}>
      {/*
        ── WHAT IS ON THE PAPER AND WHAT IS NOT ──────────────────────────
        Everything here is a fact the server returned about THIS sale.
        There is deliberately no tax PIN: the organisation has no field
        holding one, and a receipt that prints a plausible-looking PIN the
        business did not give us is a false tax document. Charter 04 §IV.
      */}
      <div className={styles.slip} id="receipt-slip">
        <div className={styles.slipHead}>
          <div className={styles.slipShop}>{sale.organisation_name}</div>
          {sale.branch_name ? (
            <div className={styles.slipWhere}>
              {sale.branch_name}
              {sale.branch_location ? ` · ${sale.branch_location}` : ""}
            </div>
          ) : null}
        </div>

        <dl className={styles.slipFacts}>
          <div>
            <dt>Receipt</dt>
            <dd>{sale.receipt?.number ?? sale.number}</dd>
          </div>
          <div>
            <dt>Date</dt>
            <dd>{stamp(issued)}</dd>
          </div>
          {sale.register_name ? (
            <div>
              <dt>Till</dt>
              <dd>{sale.register_name}</dd>
            </div>
          ) : null}
          {sale.cashier_name ? (
            <div>
              <dt>Served by</dt>
              <dd>{sale.cashier_name}</dd>
            </div>
          ) : null}
          {/* Only when a customer was actually attached to the sale. A
              receipt addressed to nobody is worse than an unaddressed one. */}
          {sale.customer_name ? (
            <div>
              <dt>Customer</dt>
              <dd>
                {sale.customer_name}
                {sale.customer_phone ? ` · ${sale.customer_phone}` : ""}
              </dd>
            </div>
          ) : null}
        </dl>

        <div className={styles.slipLines}>
          {sale.items.map((item, index) => (
            <div key={index} className={styles.slipLine}>
              <div className={styles.slipName}>
                {item.product_name}
                {item.sku ? (
                  <span className={styles.slipSku}>{item.sku}</span>
                ) : null}
              </div>
              {/* The arithmetic spelled out, because "× 3  189.00" invites
                  the question this line answers. */}
              <div className={styles.slipQty}>
                {Number(item.quantity)} × {shillings(cents(item.unit_price))}
                {/*
                  The old price struck through, beside the one charged.
                  A saving stated without the price it is measured from is
                  a number a customer has to take on trust.
                */}
                {cents(item.saved) > 0 ? (
                  <>
                    {" "}
                    <s className={styles.slipWas}>
                      {shillings(cents(item.usual_price))}
                    </s>
                  </>
                ) : null}
              </div>
              {cents(item.saved) > 0 ? (
                <div className={styles.slipOffer}>
                  {item.offer_name || "Offer"} — saved{" "}
                  {shillings(cents(item.saved))}
                </div>
              ) : null}
              <div className={styles.slipAmount}>
                {shillings(cents(item.line_total))}
              </div>
            </div>
          ))}
        </div>

        <div className={styles.slipSums}>
          <div>
            <span>Subtotal</span>
            <span>{shillings(cents(sale.subtotal))}</span>
          </div>
          {discount > 0 ? (
            <div>
              <span>Discount</span>
              <span>−{shillings(discount)}</span>
            </div>
          ) : null}
          <div>
            {/* Kenyan VAT is charged inside the price, so this is a
                breakdown of the total and not an addition to it. Saying
                "included" is the difference between a customer reading
                the total as 1,030 and as 1,172. */}
            <span>VAT (included)</span>
            <span>{shillings(cents(sale.tax_total))}</span>
          </div>
          <div className={styles.slipTotal}>
            <span>Total</span>
            <span>{shillings(cents(sale.total))}</span>
          </div>
          {/*
            Under the total, where a supermarket puts it, and only when
            there is something to say. It is the line a customer reads
            aloud to whoever they came with.
          */}
          {saved > 0 ? (
            <div className={styles.slipSaved}>
              <span>You saved today</span>
              <span>{shillings(saved)}</span>
            </div>
          ) : null}
        </div>

        <div className={styles.slipPaid}>
          {sale.payments.map((paid, index) => (
            <div key={index}>
              <div className={styles.slipPaidRow}>
                <span>{paid.method_label}</span>
                <span>{shillings(cents(paid.amount))}</span>
              </div>
              {/* The M-Pesa code is what a customer matches against the SMS
                  on their phone, and the only thing that settles an
                  argument about whether they paid. */}
              {paid.reference ? (
                <div className={styles.slipRef}>Ref {paid.reference}</div>
              ) : null}
              {paid.tendered && Number(paid.tendered) > 0 ? (
                <>
                  <div className={styles.slipPaidRow}>
                    <span>Cash given</span>
                    <span>{shillings(cents(paid.tendered))}</span>
                  </div>
                  <div className={styles.slipPaidRow}>
                    <span>Change</span>
                    <span>{shillings(cents(paid.change_given ?? "0"))}</span>
                  </div>
                </>
              ) : null}
            </div>
          ))}
        </div>

        <div className={styles.slipFoot}>
          <div>Thank you</div>
          <div className={styles.slipFine}>
            Keep this receipt. Returns need it.
          </div>
        </div>
      </div>

      <div className={styles.slipActions} id="receipt-actions">
        <div className={styles.slipButtons}>
          <button
            className={styles.slipPrint}
            onClick={() => window.print()}
            type="button"
          >
            Print
          </button>
          <button className={styles.take} onClick={onNext} autoFocus>
            Next customer
          </button>
        </div>
        <label className={styles.slipAuto}>
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => {
              setAuto(e.target.checked);
              rememberAutoPrint(e.target.checked);
            }}
          />
          Print every receipt automatically on this till
        </label>
      </div>
    </div>
  );
}
