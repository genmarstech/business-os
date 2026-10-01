"use client";

import { useActionState, useState } from "react";

import { savePriceList, setListActive, type State } from "./actions";
import type { Branch, PriceList, Product } from "./types";
import styles from "./prices.module.css";

const EMPTY: State = null;

type Line = { key: number; product: string; price: string };

let counter = 0;
function blank(): Line {
  counter += 1;
  return { key: counter, product: "", price: "" };
}

/**
 * Making or changing a price list.
 *
 * ── THE PRICES ARE EDITED IN THE SAME FORM AS THE LIST ────────────────────
 * A list with no prices on it does nothing, so splitting "create the list"
 * from "put prices on it" would make the first step a thing that looks
 * finished and is not. The server takes both in one document for the same
 * reason.
 */
export function PriceListForm({
  organizationId,
  branches,
  products,
  existing,
  onDone,
}: {
  organizationId: number;
  branches: Branch[];
  products: Product[];
  existing?: PriceList;
  onDone?: () => void;
}) {
  const [state, action, pending] = useActionState(savePriceList, EMPTY);
  const [lines, setLines] = useState<Line[]>(() =>
    existing && existing.entries.length > 0
      ? existing.entries.map((entry) => ({
          key: (counter += 1),
          product: String(entry.product),
          price: entry.price,
        }))
      : [blank()],
  );

  const chosen = new Set(lines.map((line) => line.product).filter(Boolean));

  function update(key: number, field: "product" | "price", value: string) {
    setLines((current) =>
      current.map((line) =>
        line.key === key ? { ...line, [field]: value } : line,
      ),
    );
  }

  return (
    <form action={action} className={styles.form}>
      {existing ? <input type="hidden" name="id" value={existing.id} /> : null}
      <input type="hidden" name="organization" value={organizationId} />

      <div className={styles.fieldRow}>
        <label className={styles.field}>
          <span className={styles.label}>Name</span>
          <input
            name="name"
            className={styles.input}
            required
            maxLength={120}
            defaultValue={existing?.name}
            placeholder="October promotion"
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Precedence</span>
          <input
            name="precedence"
            type="number"
            min={0}
            max={32000}
            required
            className={styles.input}
            defaultValue={existing?.precedence ?? 10}
          />
          {/*
            The one field that needs explaining, so it is explained here
            rather than in a help page nobody opens.
          */}
          <span className={styles.hint}>
            Higher wins where two lists both apply. No two lists in this
            business may share a number.
          </span>
        </label>
      </div>

      <div className={styles.fieldRow}>
        <label className={styles.field}>
          <span className={styles.label}>Starts (optional)</span>
          <input
            name="starts_on"
            type="date"
            className={styles.input}
            defaultValue={existing?.starts_on ?? ""}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Ends (optional)</span>
          <input
            name="ends_on"
            type="date"
            className={styles.input}
            defaultValue={existing?.ends_on ?? ""}
          />
          <span className={styles.hint}>
            Both days are included. Leave either blank for no limit.
          </span>
        </label>
      </div>

      <fieldset className={styles.fieldset}>
        <legend className={styles.label}>Branches</legend>
        <p className={styles.hint}>
          Tick none and it applies everywhere.
        </p>
        <div className={styles.checks}>
          {branches.map((branch) => (
            <label key={branch.id} className={styles.check}>
              <input
                type="checkbox"
                name="branch"
                value={branch.id}
                defaultChecked={existing?.branches.includes(branch.id)}
              />
              {branch.branch_name}
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className={styles.fieldset}>
        <legend className={styles.label}>Prices</legend>
        <p className={styles.hint}>
          Only what you list here changes. Everything you leave off keeps its
          usual price.
        </p>
        {lines.map((line) => {
          const product = products.find((p) => String(p.id) === line.product);
          return (
            <div key={line.key} className={styles.line}>
              <select
                name="product"
                className={styles.select}
                value={line.product}
                onChange={(e) => update(line.key, "product", e.target.value)}
              >
                <option value="">Choose a product</option>
                {products
                  .filter(
                    (p) =>
                      String(p.id) === line.product ||
                      !chosen.has(String(p.id)),
                  )
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
              </select>
              <input
                name="price"
                className={styles.lineInput}
                inputMode="decimal"
                placeholder="0.00"
                value={line.price}
                onChange={(e) => update(line.key, "price", e.target.value)}
              />
              <span className={styles.was}>
                {product ? `was ${product.selling_price}` : ""}
              </span>
              <button
                type="button"
                className={styles.drop}
                onClick={() =>
                  setLines((current) =>
                    current.length === 1
                      ? [blank()]
                      : current.filter((l) => l.key !== line.key),
                  )
                }
                aria-label="Remove this line"
              >
                ×
              </button>
            </div>
          );
        })}
        <button
          type="button"
          className={styles.quietButton}
          onClick={() => setLines((current) => [...current, blank()])}
        >
          Add another
        </button>
      </fieldset>

      {state?.general?.length ? (
        <p className={styles.error}>{state.general[0]}</p>
      ) : null}
      {Object.entries(state?.field ?? {}).map(([field, message]) => (
        <p key={field} className={styles.error}>
          {field}: {message}
        </p>
      ))}

      <div className={styles.actions}>
        <button type="submit" className={styles.primary} disabled={pending}>
          {pending ? "Saving…" : existing ? "Save changes" : "Create the list"}
        </button>
        {onDone ? (
          <button
            type="button"
            className={styles.quietButton}
            onClick={onDone}
          >
            Cancel
          </button>
        ) : null}
      </div>
    </form>
  );
}

/** Show the form only once somebody asks for it. */
export function NewPriceList(props: {
  organizationId: number;
  branches: Branch[];
  products: Product[];
}) {
  const [open, setOpen] = useState(false);
  if (!open) {
    return (
      <button
        type="button"
        className={styles.primary}
        onClick={() => setOpen(true)}
      >
        New price list
      </button>
    );
  }
  return <PriceListForm {...props} onDone={() => setOpen(false)} />;
}

/**
 * Turning a list off.
 *
 * Deactivated, never deleted: a list that has priced real sales is the only
 * explanation of why they were charged what they were, and `SaleItem.price_list`
 * points at it.
 */
export function ToggleList({
  id,
  isActive,
}: {
  id: number;
  isActive: boolean;
}) {
  const [state, action, pending] = useActionState(setListActive, EMPTY);

  return (
    <form action={action} className={styles.inlineForm}>
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="is_active" value={String(!isActive)} />
      {state?.general?.length ? (
        <span className={styles.error}>{state.general[0]}</span>
      ) : null}
      <button type="submit" className={styles.quietButton} disabled={pending}>
        {pending ? "…" : isActive ? "Turn off" : "Turn on"}
      </button>
    </form>
  );
}
