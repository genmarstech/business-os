"use client";

import { useActionState } from "react";

import { General, Row, Select, Submit, Text } from "@/components/Form";
import {
  createBranch,
  createBusiness,
  createFirstProduct,
  createRegister,
  type State,
} from "./actions";
import styles from "./welcome.module.css";

/**
 * The four onboarding forms.
 *
 * Client components because each holds the server action's returned errors,
 * which is the only state any of them has. The writes themselves happen on
 * the server — see actions.ts.
 *
 * ── THE COPY IS PART OF THE WORK ───────────────────────────────────────────
 * Every hint here says something the person could not have guessed and would
 * otherwise discover from a rejected form or, worse, from a wrong number on a
 * receipt. That is the difference between a field and a form somebody can
 * fill in without asking anyone.
 */

const NONE: State = null;

export function BusinessForm() {
  const [state, action] = useActionState(createBusiness, NONE);

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>What is the business called?</h2>
      <p className={styles.cardLede}>
        The name your customers know. It goes on receipts and it is what you
        will see at the top of every screen.
      </p>

      <form action={action}>
        <General messages={state?.general ?? []} />

        <Text
          name="name"
          label="Business name"
          placeholder="Mwangi Stores"
          required
          autoFocus
          error={state?.field.name}
        />

        <Select
          name="staff_size"
          label="Roughly how many people work there?"
          hint="Only used to size the plan. It changes nothing you can do."
          defaultValue="MD"
          error={state?.field.staff_size}
        >
          <option value="SM">Around 5</option>
          <option value="MD">Around 15</option>
          <option value="LG">25 or more</option>
        </Select>

        <Submit pending="Creating…">Create business</Submit>
      </form>

      <div className={styles.note}>
        <strong>Two shops may share a name.</strong> Yours is identified by a
        number we generate, not by the name, so nobody else registering
        &ldquo;Mwangi Stores&rdquo; affects you and you will never be told the
        name is taken.
      </div>
    </section>
  );
}

export function BranchForm({ organisationId }: { organisationId: number }) {
  const [state, action] = useActionState(createBranch, NONE);

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>Where do you sell?</h2>
      <p className={styles.cardLede}>
        A branch is a physical location. Stock, tills, staff and takings all
        belong to one — which is what lets you compare them later.
      </p>

      <form action={action}>
        <input type="hidden" name="organization_id" value={organisationId} />
        <General messages={state?.general ?? []} />

        <Row>
          <Text
            name="branch_name"
            label="Branch name"
            placeholder="Westlands"
            required
            autoFocus
            error={state?.field.branch_name}
          />
          <Text
            name="branch_location"
            label="Town or area"
            placeholder="Nairobi"
            required
            error={state?.field.branch_location}
          />
        </Row>

        <Row>
          <Text
            name="branch_allocation"
            label="Where in the building"
            hint="Optional. Ground floor, Shop 4, that sort of thing."
            placeholder="Ground floor"
            error={state?.field.branch_allocation}
          />
          <Text
            name="branch_manager"
            label="Who runs it"
            placeholder="A Manager"
            required
            error={state?.field.branch_manager}
          />
        </Row>

        <Submit pending="Creating…">Create branch</Submit>
      </form>

      <div className={styles.note}>
        You can add more later. If you only have one shop, one branch is the
        right answer — nothing here assumes a chain.
      </div>
    </section>
  );
}

export function ProductForm({
  organisationId,
  hasTaxRule,
  branchId,
}: {
  organisationId: number;
  hasTaxRule: boolean;
  /** Where the opening count goes. The flow has made exactly one by now. */
  branchId: number | null;
}) {
  const [state, action] = useActionState(createFirstProduct, NONE);

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>Add something you sell</h2>
      <p className={styles.cardLede}>
        One product is enough to get a till working. The rest of the catalogue
        can wait, and can be imported later.
      </p>

      <form action={action}>
        <input type="hidden" name="organization_id" value={organisationId} />
        {branchId ? (
          <input type="hidden" name="branch_id" value={branchId} />
        ) : null}
        <General messages={state?.general ?? []} />

        <Text
          name="name"
          label="Product name"
          placeholder="Milk 500ml"
          required
          autoFocus
          error={state?.field.name}
        />

        <Row>
          <Text
            name="sku"
            label="Your own code"
            hint="However you already refer to it on a shelf label."
            placeholder="MILK-500"
            mono
            required
            error={state?.field.sku}
          />
          <Text
            name="barcode"
            label="Barcode"
            hint="Optional. Scan into this field if it has one — that is what the till will match."
            placeholder=""
            mono
            error={state?.field.barcode}
          />
        </Row>

        <Row>
          <Text
            name="selling_price"
            label="Shelf price"
            hint="What the customer pays, tax included."
            placeholder="116.00"
            inputMode="decimal"
            mono
            required
            error={state?.field.selling_price}
          />
          <Text
            name="cost_price"
            label="What it costs you"
            hint="Optional, but it is what makes the profit figures real."
            placeholder="70.00"
            inputMode="decimal"
            mono
            error={state?.field.cost_price}
          />
        </Row>

        {hasTaxRule ? null : (
          <Text
            name="tax_rate"
            label="VAT rate"
            hint="Standard rate in Kenya is 16. Leave blank if you are not registered for VAT."
            placeholder="16"
            inputMode="decimal"
            mono
            defaultValue="16"
            error={state?.field.tax_rate}
          />
        )}

        {branchId ? (
          <Text
            name="opening_stock"
            label="How many do you have?"
            hint="Booked in as your first delivery, so the number has a record behind it. A till will not sell what is not on the shelf."
            inputMode="decimal"
            mono
            defaultValue="0"
            error={state?.field.opening_stock}
          />
        ) : null}

        <Text
          name="category_name"
          label="Category"
          hint="Anything. It is how the till groups products on screen."
          placeholder="General"
          defaultValue="General"
          error={state?.field.category_name}
        />

        <Submit pending="Adding…">Add product</Submit>
      </form>

      <div className={styles.note}>
        <strong>The shelf price includes the tax.</strong> At 16%, a price of
        116 means the customer pays 116 and 16 of it is VAT — the total will
        not jump when they reach the payment step. Every sale copies the rate
        it was charged at, so changing it later never rewrites an old receipt.
      </div>
    </section>
  );
}

export function RegisterForm({
  branches,
}: {
  branches: { id: number; branch_name: string }[];
}) {
  const [state, action] = useActionState(createRegister, NONE);

  return (
    <section className={styles.card}>
      <h2 className={styles.cardTitle}>Set up a till</h2>
      <p className={styles.cardLede}>
        A register is one point of sale — a counter, a tablet, a lane. Sales
        and cash are counted per register, which is how a drawer gets
        reconciled at the end of a shift.
      </p>

      <form action={action}>
        <General messages={state?.general ?? []} />

        <Select
          name="branch_id"
          label="At which branch"
          required
          error={state?.field.branch_id}
        >
          {branches.map((branch) => (
            <option key={branch.id} value={branch.id}>
              {branch.branch_name}
            </option>
          ))}
        </Select>

        <Row>
          <Text
            name="name"
            label="What to call it"
            placeholder="Till 1"
            required
            defaultValue="Till 1"
            error={state?.field.name}
          />
          <Text
            name="register_number"
            label="Number"
            hint="Yours to choose. It only has to be unique within the branch."
            placeholder="T1"
            mono
            required
            defaultValue="T1"
            error={state?.field.register_number}
          />
        </Row>

        <Submit pending="Creating…">Create till</Submit>
      </form>

      <div className={styles.note}>
        Every shop calls its first till &ldquo;Till 1&rdquo;, so the name only
        has to be unique inside this branch — another branch may use it too.
      </div>
    </section>
  );
}
