"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Scanner } from "./Scanner";
import { scanning } from "./barcode";
import { call } from "./session";
import styles from "./count.module.css";

/**
 * Counting a shelf, from the aisle.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE COUNT IS BLIND. THE EXPECTED FIGURE IS NEVER ON THIS SCREEN.
 *
 * Show somebody the number the system believes and they will count to it.
 * Not dishonestly — a shelf of identical boxes is genuinely hard to count,
 * "it says 50" is a powerful suggestion, and the count stops detecting
 * anything at all. Every retail stock-take control starts here.
 *
 * The API returns `expected_quantity` on a line, because the review sheet
 * needs it. This screen reads it and does not render it, and the variance
 * is revealed on the review tab, which needs a permission the person
 * counting does not have to hold.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── EVERY LINE POSTS THE MOMENT IT IS ENTERED ─────────────────────────────
 *
 * Not batched, not held until the end. Somebody is walking an aisle with a
 * phone and the phone will ring, lock, run out of battery or be put down on
 * a shelf. A count that lived in browser state until a Save button would
 * lose the morning, and the person who lost it would never do a second one.
 */

type Line = {
  id: number;
  inventory: number;
  product_name: string;
  product_sku: string;
  expected_quantity: string;
  counted_quantity: string;
  variance: string;
  counted_by_name: string;
  note: string;
};

type Count = {
  id: number;
  number: number;
  branch: number;
  branch_name: string;
  status: "open" | "closed" | "abandoned";
  status_label: string;
  opened_by_name: string;
  lines: Line[];
  summary: {
    counted: number;
    agreed: number;
    short: number;
    over: number;
    units_short: string;
    units_over: string;
  };
};

type Stock = {
  id: number;
  branch: number;
  product_name: string;
  product_sku: string;
  /**
   * The product's barcode, read through the join on BranchInventory.
   *
   * Optional because most shops have products without one — loose goods,
   * anything sold by weight — and because a count screen served by an API
   * that predates the field must not break.
   */
  product_barcode?: string;
  is_active: boolean;
};

type Branch = { id: number; branch_name: string; is_active: boolean };
type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T>): T[] {
  return Array.isArray(page) ? page : (page.results ?? []);
}

function message(error: unknown): string {
  if (error && typeof error === "object") {
    const body = error as Record<string, unknown>;
    if (typeof body.detail === "string") return body.detail;
    for (const value of Object.values(body)) {
      if (typeof value === "string") return value;
      if (Array.isArray(value) && typeof value[0] === "string") return value[0];
    }
  }
  return "That did not save. Try again.";
}

export function Count({
  permissions,
  onLeave,
}: {
  permissions: string[];
  onLeave: () => void;
}) {
  const mayClose = permissions.includes("inventory.count.close");

  const [branches, setBranches] = useState<Branch[]>([]);
  const [count, setCount] = useState<Count | null>(null);
  const [stock, setStock] = useState<Stock[]>([]);
  const [tab, setTab] = useState<"count" | "review">("count");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError("");
    try {
      const [branchPage, countPage] = await Promise.all([
        call<Page<Branch>>("/brn/branch/"),
        call<Page<Count>>("/invt/stock-counts/"),
      ]);
      setBranches(rows(branchPage).filter((b) => b.is_active));
      const open = rows(countPage).find((c) => c.status === "open") ?? null;
      setCount(open);
      if (open) {
        const stockPage = await call<Page<Stock>>("/invt/inventory/");
        setStock(rows(stockPage).filter((s) => s.is_active && s.branch === open.branch));
      }
    } catch (err) {
      setError(message(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function openCount(branchId: number) {
    setBusy(true);
    setError("");
    try {
      await call<Count>("/invt/stock-counts/open/", {
        method: "POST",
        // No `opened_by`. The server signs every one of these with the
        // session the request arrived on — a till saying who is counting is
        // a till that can say somebody else. See inventory/services._actor.
        body: { branch: branchId },
      });
      await load();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className={styles.boot}>Loading…</div>;

  return (
    <div className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <p className={styles.eyebrow}>Stock count</p>
          <h1 className={styles.title}>
            {count ? `Count ${count.number} · ${count.branch_name}` : "Count the shelves"}
          </h1>
        </div>
        <button type="button" className={styles.ghost} onClick={onLeave}>
          Done
        </button>
      </header>

      {error && <p className={styles.error}>{error}</p>}

      {!count ? (
        <StartCount branches={branches} busy={busy} onStart={openCount} />
      ) : (
        <>
          <nav className={styles.tabs} role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "count"}
              className={styles.tab}
              onClick={() => setTab("count")}
            >
              Counting
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "review"}
              className={styles.tab}
              onClick={() => setTab("review")}
            >
              {mayClose ? "Review and close" : "What I have counted"}
            </button>
          </nav>

          {tab === "count" ? (
            <Counting count={count} stock={stock} onCounted={load} />
          ) : (
            <Review
              count={count}
              mayClose={mayClose}
              onChanged={async () => {
                await load();
                setTab("count");
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

function StartCount({
  branches,
  busy,
  onStart,
}: {
  branches: Branch[];
  busy: boolean;
  onStart: (branchId: number) => void;
}) {
  const [branch, setBranch] = useState<string>("");

  return (
    <div className={styles.panel}>
      <p className={styles.lede}>
        Pick a branch and start counting. One count runs at a branch at a time,
        so if somebody else is already counting there, finish theirs first.
      </p>
      <label className={styles.field}>
        <span className={styles.label}>Branch</span>
        <select
          className={styles.input}
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
        >
          <option value="">Choose…</option>
          {branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.branch_name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className={styles.primary}
        disabled={!branch || busy}
        onClick={() => onStart(Number(branch))}
      >
        {busy ? "…" : "Start counting"}
      </button>
    </div>
  );
}

/** The aisle. Search, tap, type, next. */
function Counting({
  count,
  stock,
  onCounted,
}: {
  count: Count;
  stock: Stock[];
  onCounted: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [entry, setEntry] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [camera, setCamera] = useState(false);
  const search = useRef<HTMLInputElement>(null);

  // Asked after mount: `scanning()` reads `window`, and calling it during
  // render would disagree with the server's render and throw a hydration
  // error.
  const [canScan, setCanScan] = useState(false);
  useEffect(() => setCanScan(scanning()), []);

  // What has been counted already, by inventory id. The quantity is shown
  // back so somebody can see their own work; the EXPECTED figure in the same
  // line object is deliberately never read here. See the banner above.
  const counted = useMemo(
    () => new Map(count.lines.map((l) => [l.inventory, l.counted_quantity])),
    [count.lines],
  );

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = needle
      ? stock.filter(
          (s) =>
            s.product_name.toLowerCase().includes(needle) ||
            s.product_sku.toLowerCase().includes(needle) ||
            // Exact, unlike the two above. A partial match on thirteen digits
            // is a different product, and the point of a code is that it
            // either is this row's or is not.
            s.product_barcode?.toLowerCase() === needle,
        )
      : stock;
    // Uncounted first: the job is finding what has not been done yet, and a
    // list that keeps finished rows at the top makes somebody scroll past
    // their own progress every time.
    return [...list].sort((a, b) => {
      const done = Number(counted.has(a.id)) - Number(counted.has(b.id));
      return done !== 0 ? done : a.product_name.localeCompare(b.product_name);
    });
  }, [stock, query, counted]);

  /**
   * A scanned code opens that shelf row with the number pad focused.
   *
   * ══════════════════════════════════════════════════════════════════════
   * THE CAMERA CLOSES ON A HIT, UNLIKE THE TILL'S.
   *
   * At a register the next thing after a scan is another scan. In an aisle
   * the next thing is TYPING — how many are on the shelf — so a camera that
   * stayed open would cover the number pad with a live preview. Scan, type,
   * Enter, scan the next: the phone is held in one hand and the other is on
   * the shelf.
   * ══════════════════════════════════════════════════════════════════════
   *
   * The search box is cleared rather than set to the code, so the row is
   * found in the full list and `autoFocus` on the entry field scrolls it into
   * view. Leaving a filter behind would hide everything else the moment the
   * count was saved.
   */
  const scanned = useCallback(
    (code: string): boolean => {
      const needle = code.trim().toLowerCase();
      const row = stock.find(
        (s) => s.product_barcode?.toLowerCase() === needle,
      );
      if (!row) return false;
      setQuery("");
      setError("");
      setOpen(row.id);
      setEntry(counted.get(row.id) ?? "");
      return true;
    },
    [stock, counted],
  );

  async function save(inventoryId: number) {
    const value = entry.trim();
    if (value === "" || Number(value) < 0 || Number.isNaN(Number(value))) {
      setError("How many are on the shelf?");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await call(`/invt/stock-counts/${count.id}/record/`, {
        method: "POST",
        body: { inventory: inventoryId, counted: value },
      });
      setOpen(null);
      setEntry("");
      await onCounted();
      search.current?.focus();
    } catch (err) {
      setError(message(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className={styles.progress}>
        <strong>{count.summary.counted}</strong> of {stock.length} counted
        <span className={styles.dim}>
          {" "}
          · {stock.length - count.summary.counted} to go
        </span>
      </div>

      {camera ? (
        <Scanner
          title="Scan a shelf label"
          hint="Point at the barcode. It opens that product so you can type the count."
          // Closes on a hit — see the banner on `scanned`.
          continuous={false}
          onScan={scanned}
          onClose={() => setCamera(false)}
        />
      ) : null}

      <div className={styles.findRow}>
        <input
          ref={search}
          className={styles.search}
          type="search"
          inputMode="search"
          placeholder="Barcode, product name or SKU"
          aria-label="Find a product"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {/*
          Only where the browser can do it — see the banner on barcode.ts.
          Absent rather than disabled: on an iPhone a dead button invites
          somebody to keep pressing it, and the field beside it still takes a
          typed or wedge-scanned code on every device.
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

      {error && <p className={styles.error}>{error}</p>}

      {matches.length === 0 ? (
        <p className={styles.empty}>Nothing here matches “{query}”.</p>
      ) : (
        <ul className={styles.list}>
          {matches.map((s) => {
            const done = counted.get(s.id);
            const isOpen = open === s.id;
            return (
              <li key={s.id} className={`${styles.row} ${done ? styles.done : ""}`}>
                <button
                  type="button"
                  className={styles.rowHead}
                  aria-expanded={isOpen}
                  onClick={() => {
                    setOpen(isOpen ? null : s.id);
                    setEntry(done ?? "");
                    setError("");
                  }}
                >
                  <span className={styles.name}>
                    {s.product_name}
                    <span className={styles.sku}>{s.product_sku}</span>
                  </span>
                  <span className={done ? styles.tally : styles.todo}>
                    {done !== undefined ? Number(done) : "count"}
                  </span>
                </button>

                {isOpen && (
                  <div className={styles.entry}>
                    <input
                      className={styles.number}
                      type="number"
                      min={0}
                      step="any"
                      inputMode="decimal"
                      autoFocus
                      aria-label={`Counted quantity for ${s.product_name}`}
                      value={entry}
                      onChange={(e) => setEntry(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void save(s.id);
                        if (e.key === "Escape") setOpen(null);
                      }}
                    />
                    <button
                      type="button"
                      className={styles.primary}
                      disabled={saving}
                      onClick={() => void save(s.id)}
                    >
                      {saving ? "…" : done !== undefined ? "Correct it" : "Save"}
                    </button>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

/**
 * The sheet, and the signature.
 *
 * This is where the expected figure appears, and it is the only place. For
 * somebody without the closing permission it is still useful — it is their
 * own work, listed — but the variance columns and the button are absent,
 * because what they would be looking at is a decision that is not theirs.
 */
function Review({
  count,
  mayClose,
  onChanged,
}: {
  count: Count;
  mayClose: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [abandoning, setAbandoning] = useState(false);
  const [reason, setReason] = useState("");

  async function act(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await call(`/invt/stock-counts/${count.id}/${path}/`, { method: "POST", body });
      await onChanged();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  const s = count.summary;

  return (
    <div className={styles.review}>
      {/*
        * `agreed` is withheld too, not just the columns.
        *
        * It looks harmless and is not: "2 counted, 1 agreed" tells somebody
        * that one of their two counts disagreed with the system, which is a
        * fact derived from the expected figure. On the next shelf they would
        * know to look twice at the one that felt wrong — and a blind count
        * that leaks a warm-or-cold signal is not a blind count.
        */}
      <dl className={styles.tallies}>
        <div><dt>Counted</dt><dd>{s.counted}</dd></div>
        {mayClose && (
          <>
            <div><dt>Agreed</dt><dd>{s.agreed}</dd></div>
            <div className={styles.short}>
              <dt>Short</dt><dd>{s.short}</dd>
            </div>
            <div className={styles.over}>
              <dt>Over</dt><dd>{s.over}</dd>
            </div>
          </>
        )}
      </dl>

      {count.lines.length === 0 ? (
        <p className={styles.empty}>Nothing counted yet.</p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.sheet}>
            <thead>
              <tr>
                <th scope="col">Product</th>
                {mayClose && <th scope="col">System</th>}
                <th scope="col">Counted</th>
                {mayClose && <th scope="col">Difference</th>}
              </tr>
            </thead>
            <tbody>
              {count.lines.map((l) => {
                const v = Number(l.variance);
                return (
                  <tr key={l.id}>
                    <th scope="row">
                      {l.product_name}
                      <span className={styles.sku}>{l.product_sku}</span>
                    </th>
                    {mayClose && <td>{Number(l.expected_quantity)}</td>}
                    <td>{Number(l.counted_quantity)}</td>
                    {mayClose && (
                      <td className={v < 0 ? styles.shortCell : v > 0 ? styles.overCell : ""}>
                        {v > 0 ? `+${v}` : v}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {error && <p className={styles.error}>{error}</p>}

      {!mayClose ? (
        <p className={styles.note}>
          A manager closes the count and books what it found. Tell them it is
          ready.
        </p>
      ) : (
        <div className={styles.actions}>
          {confirming ? (
            <div className={styles.confirm}>
              <p>
                This books {s.short + s.over} adjustment
                {s.short + s.over === 1 ? "" : "s"} and cannot be undone. A
                miscount is corrected by counting again, not by reopening this.
              </p>
              <button
                type="button"
                className={styles.primary}
                disabled={busy}
                onClick={() => void act("close", {})}
              >
                {busy ? "…" : "Close and book it"}
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => setConfirming(false)}
              >
                Not yet
              </button>
            </div>
          ) : (
            <button
              type="button"
              className={styles.primary}
              onClick={() => setConfirming(true)}
            >
              Close the count
            </button>
          )}

          {abandoning ? (
            <div className={styles.confirm}>
              <label className={styles.field}>
                <span className={styles.label}>Why is this being abandoned?</span>
                <input
                  className={styles.input}
                  value={reason}
                  placeholder="Counted the wrong aisle"
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
              <button
                type="button"
                className={styles.danger}
                disabled={busy || !reason.trim()}
                onClick={() =>
                  void act("abandon", { reason })
                }
              >
                {busy ? "…" : "Abandon it"}
              </button>
              <button
                type="button"
                className={styles.ghost}
                onClick={() => setAbandoning(false)}
              >
                Keep counting
              </button>
            </div>
          ) : (
            <button
              type="button"
              className={styles.linkQuiet}
              onClick={() => setAbandoning(true)}
            >
              Abandon this count
            </button>
          )}
        </div>
      )}
    </div>
  );
}
