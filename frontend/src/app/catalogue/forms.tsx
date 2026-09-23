"use client";

import { useActionState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import {
  addCategory,
  saveProduct,
  saveTaxRule,
  type State,
} from "./actions";
import type { Category, Product, TaxRule } from "./shape";
import styles from "./catalogue.module.css";

const NONE: State = null;

function saved(state: State): boolean {
  return (
    state !== null &&
    state.general.length === 0 &&
    Object.keys(state.field).length === 0
  );
}

export function ProductForm({
  organisationId,
  categories,
  taxRules,
  branches,
  product,
}: {
  organisationId: number;
  categories: Category[];
  taxRules: TaxRule[];
  /** Somewhere to put the opening count. Only asked when adding. */
  branches: { id: number; branch_name: string }[];
  product?: Product;
}) {
  const [state, action] = useActionState(saveProduct, NONE);
  const editing = Boolean(product);
  const currentCategory =
    product && typeof product.category === "object" && product.category
      ? product.category.id
      : (product?.category as number | null | undefined);

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisationId} />
      {product ? (
        <input type="hidden" name="product_id" value={product.id} />
      ) : null}

      <General messages={state?.general ?? []} />
      {saved(state) ? (
        <p className={styles.saved}>
          {editing ? "Saved. New sales use it; old receipts are unchanged." : "Added."}
        </p>
      ) : null}

      <Row>
        <Text
          name="name"
          label="Name"
          placeholder="Milk 500ml"
          required
          defaultValue={product?.name}
          error={state?.field.name}
        />
        <Select
          name="category"
          label="Category"
          hint="How the till groups it on screen."
          defaultValue={currentCategory ?? undefined}
          error={state?.field.category}
        >
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </Select>
      </Row>

      <Row>
        <Text
          name="sku"
          label="Your own code"
          placeholder="MILK-500"
          mono
          required
          defaultValue={product?.sku}
          error={state?.field.sku}
        />
        <Text
          name="barcode"
          label="Barcode"
          hint="Scan into this field. It is what the till matches a scan against."
          mono
          defaultValue={product?.barcode ?? ""}
          error={state?.field.barcode}
        />
      </Row>

      <Row>
        <Text
          name="selling_price"
          label="Shelf price"
          hint="What the customer pays."
          inputMode="decimal"
          mono
          required
          defaultValue={product?.selling_price}
          error={state?.field.selling_price}
        />
        <Text
          name="cost_price"
          label="What it costs you"
          hint="What makes the profit figures real. Nobody but you sees it."
          inputMode="decimal"
          mono
          defaultValue={product?.cost_price}
          error={state?.field.cost_price}
        />
      </Row>

      <Row>
        <Select
          name="tax_rule"
          label="Tax"
          hint="The rate this price is quoted against."
          defaultValue={product?.tax_rule ?? undefined}
          error={state?.field.tax_rule}
        >
          <option value="">No tax</option>
          {taxRules
            .filter((rule) => rule.is_active)
            .map((rule) => (
              <option key={rule.id} value={rule.id}>
                {rule.name} — {rule.rate}%{" "}
                {rule.is_inclusive ? "(in the price)" : "(added on)"}
              </option>
            ))}
        </Select>

        <Select
          name="is_active"
          label="On sale"
          hint="Off takes it out of the till without deleting its history."
          defaultValue={product?.is_active === false ? "false" : "true"}
          error={state?.field.is_active}
        >
          <option value="true">Yes</option>
          <option value="false">No — withdrawn</option>
        </Select>
      </Row>

      {/*
        ── ONLY WHEN ADDING, AND ONLY IF THERE IS A BRANCH ──────────────────
        A product on no shelf cannot be sold, and the till's refusal names the
        branch rather than the omission. Editing an existing product leaves
        its stock alone: that is what /stock is for, and a quantity quietly
        changed by a price edit is the worst kind of surprise.
      */}
      {!editing && branches.length > 0 ? (
        <Row>
          <Select
            name="stock_branch"
            label="Stock it at"
            defaultValue={branches[0]?.id}
            error={state?.field.stock_branch}
          >
            {branches.map((branch) => (
              <option key={branch.id} value={branch.id}>
                {branch.branch_name}
              </option>
            ))}
          </Select>
          <Text
            name="opening_stock"
            label="How many do you have?"
            hint="Booked in as a delivery, so the opening number has a record behind it."
            inputMode="decimal"
            mono
            defaultValue="0"
            error={state?.field.opening_stock}
          />
        </Row>
      ) : null}

      <Submit pending="Saving…">
        {editing ? "Save changes" : "Add to the catalogue"}
      </Submit>
    </form>
  );
}

export function CategoryForm({ organisationId }: { organisationId: number }) {
  const [state, action] = useActionState(addCategory, NONE);

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisationId} />
      <General messages={state?.general ?? []} />
      {saved(state) ? <p className={styles.saved}>Added.</p> : null}

      <Row>
        <Text
          name="name"
          label="Category"
          placeholder="Dairy"
          required
          error={state?.field.name}
        />
        <Text
          name="description"
          label="Note"
          hint="Optional. For you, not the customer."
          error={state?.field.description}
        />
      </Row>

      <Submit pending="Adding…">Add category</Submit>
    </form>
  );
}

export function TaxRuleForm({
  organisationId,
  rule,
}: {
  organisationId: number;
  rule?: TaxRule;
}) {
  const [state, action] = useActionState(saveTaxRule, NONE);

  return (
    <form action={action} className={styles.form}>
      <input type="hidden" name="organization_id" value={organisationId} />
      {rule ? <input type="hidden" name="rule_id" value={rule.id} /> : null}

      <General messages={state?.general ?? []} />
      {saved(state) ? (
        <p className={styles.saved}>
          Saved. Sales already rung up keep the rate they were charged at.
        </p>
      ) : null}

      <Row>
        <Text
          name="name"
          label="What to call it"
          placeholder="VAT 16%"
          required
          defaultValue={rule?.name}
          error={state?.field.name}
        />
        <Text
          name="rate"
          label="Rate %"
          placeholder="16"
          inputMode="decimal"
          mono
          required
          defaultValue={rule?.rate}
          error={state?.field.rate}
        />
      </Row>

      <div className={styles.checks}>
        <label className={styles.check}>
          <input
            type="checkbox"
            name="is_inclusive"
            defaultChecked={rule ? rule.is_inclusive : true}
          />
          <span>
            <strong>The shelf price already includes it.</strong> Normal in
            Kenya: a label saying 116 means the customer pays 116 and 16 of it
            is tax. Unticked, the tax is added at the till and the total jumps.
          </span>
        </label>

        <label className={styles.check}>
          <input
            type="checkbox"
            name="is_default"
            defaultChecked={rule ? rule.is_default : false}
          />
          <span>
            <strong>Use it for new products.</strong> Only one rule can be the
            default; ticking this unticks the other.
          </span>
        </label>
      </div>

      <Submit pending="Saving…">{rule ? "Save" : "Add rule"}</Submit>
    </form>
  );
}
