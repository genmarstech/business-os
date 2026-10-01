import { redirect } from "next/navigation";

import { Shell } from "@/components/Shell";
import { getOrNull } from "@/lib/api";
import { ksh } from "@/lib/money";
import { PERM, may, me as whoAmI, type SubscriptionState } from "@/lib/session";
import { CancelForm, ReinstateForm } from "./forms";
import styles from "./subscription.module.css";

/**
 * What the business pays Genmars, and until when.
 *
 * ── THE FIRST THING ON THE PAGE IS WHAT STILL WORKS ───────────────────────
 * Somebody opens this screen because a banner worried them, and the single
 * most useful sentence is that the tills, sales and stock are untouched.
 * It sits above the dates, and above the plan, for that reason — see the
 * banner in subscriptions/entitlement.py, which is the argument.
 *
 * ── THERE IS NOTHING HERE THAT TAKES A PAYMENT, ON PURPOSE ────────────────
 * The money lives at Genmars, where the invoice does. A button here that
 * extended a subscription would grant entitlement with nothing behind it,
 * and a subscriber's own session would be enough to press it. Settling up
 * is a conversation with Genmars until the integration exists, and the page
 * says so rather than offering a control that does not work.
 */

export const dynamic = "force-dynamic";

export const metadata = { title: "Subscription" };

type Plan = {
  id: number;
  code: string;
  name: string;
  description: string;
  monthly_price: string;
  branch_limit: number | null;
  staff_limit: number | null;
  register_limit: number | null;
};

type Subscription = {
  id: number;
  state: SubscriptionState;
  state_label: string;
  plan_detail: Plan | null;
  started_on: string;
  trial_ends_on: string | null;
  paid_until: string | null;
  covered_until: string | null;
  grace_until: string | null;
  grace_days: number;
  days_left: number | null;
  cancelled_on: string | null;
  cancellation_reason: string;
};

type Event = {
  id: number;
  kind: string;
  kind_label: string;
  actor_name: string;
  note: string;
  at: string;
};

type Page<T> = { results?: T[] } | T[];

function rows<T>(page: Page<T> | null): T[] {
  if (!page) return [];
  return Array.isArray(page) ? page : (page.results ?? []);
}

function when(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function chipFor(state: SubscriptionState): string {
  if (state === "active" || state === "trialing") return styles.chipGood ?? "";
  if (state === "past_due" || state === "suspended") return styles.chipWarn ?? "";
  return styles.chipQuiet ?? "";
}

/** A ceiling, where there is one. Null is no ceiling, never a ceiling of zero. */
function ceiling(value: number | null): string {
  return value === null ? "No limit" : String(value);
}

export default async function SubscriptionPage() {
  const me = await whoAmI();
  if (!me) redirect("/");

  const allowed = may(me, PERM.settingsOrganisation);
  if (!allowed) {
    return (
      <Shell me={me}>
        <div className={styles.page}>
          <header className={styles.head}>
            <p className={styles.eyebrow}>Settings</p>
            <h1 className={styles.title}>Subscription</h1>
          </header>
          <section className={styles.panel}>
            <p className={styles.empty}>
              What the business pays is the owner&rsquo;s to see. Your tills,
              sales and stock are unaffected by it either way.
            </p>
          </section>
        </div>
      </Shell>
    );
  }

  const list = rows(await getOrNull<Page<Subscription>>("/sub/subscription/"));
  const subscription = list[0];
  const plans = rows(await getOrNull<Page<Plan>>("/sub/plans/"));
  const history = subscription
    ? rows(
        await getOrNull<Page<Event>>(
          `/sub/subscription/${subscription.id}/history/`,
        ),
      )
    : [];

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <header className={styles.head}>
          <p className={styles.eyebrow}>Settings</p>
          <h1 className={styles.title}>Subscription</h1>
          <p className={styles.sub}>
            What this business pays Genmars, and until when.
          </p>
        </header>

        {!subscription ? (
          <section className={styles.panel}>
            <p className={styles.empty}>
              There is no subscription recorded for this business, and nothing
              is restricted because of it. If that is a surprise, Genmars can
              tell you what is on file.
            </p>
          </section>
        ) : (
          <>
            <section className={styles.panel}>
              <div className={styles.stateRow}>
                <span className={styles.state}>{subscription.state_label}</span>
                <span className={`${styles.chip} ${chipFor(subscription.state)}`}>
                  {subscription.plan_detail
                    ? subscription.plan_detail.name
                    : "No plan chosen"}
                </span>
              </div>

              {/*
                Above the dates, deliberately. It is the sentence somebody
                came here for, whatever the state turns out to be.
              */}
              <p className={styles.reassurance}>
                Your tills, sales, stock and reports work normally. Nothing
                about a subscription can stop this business trading.
              </p>

              <dl className={styles.facts}>
                <div className={styles.fact}>
                  <dt className={styles.factLabel}>Started</dt>
                  <dd className={styles.factValue}>
                    {when(subscription.started_on)}
                  </dd>
                </div>
                <div className={styles.fact}>
                  <dt className={styles.factLabel}>Trial ends</dt>
                  <dd className={styles.factValue}>
                    {when(subscription.trial_ends_on)}
                  </dd>
                </div>
                <div className={styles.fact}>
                  <dt className={styles.factLabel}>Paid until</dt>
                  <dd className={styles.factValue}>
                    {when(subscription.paid_until)}
                  </dd>
                </div>
                <div className={styles.fact}>
                  <dt className={styles.factLabel}>Covered until</dt>
                  <dd className={styles.factValue}>
                    {when(subscription.covered_until)}
                    {subscription.days_left !== null ? (
                      <span className={styles.meta}>
                        {subscription.days_left >= 0
                          ? `${subscription.days_left} day${subscription.days_left === 1 ? "" : "s"} left`
                          : `${-subscription.days_left} day${subscription.days_left === -1 ? "" : "s"} ago`}
                      </span>
                    ) : null}
                  </dd>
                </div>
                {subscription.cancelled_on ? (
                  <div className={styles.fact}>
                    <dt className={styles.factLabel}>Cancelled from</dt>
                    <dd className={styles.factValue}>
                      {when(subscription.cancelled_on)}
                    </dd>
                  </div>
                ) : null}
              </dl>
            </section>

            <section className={styles.panel}>
              <h2 className={styles.panelTitle}>Settling up</h2>
              <p className={styles.panelLede}>
                Payments are handled by Genmars, where your invoice is. There
                is deliberately no button here that takes money — this screen
                records what has been agreed, it does not charge for it.
              </p>
              {subscription.cancelled_on ? (
                <ReinstateForm id={subscription.id} />
              ) : (
                <CancelForm
                  id={subscription.id}
                  coveredUntil={
                    subscription.covered_until
                      ? when(subscription.covered_until)
                      : null
                  }
                />
              )}
            </section>

            {plans.length > 0 ? (
              <section className={styles.panel}>
                <h2 className={styles.panelTitle}>Plans</h2>
                <p className={styles.panelLede}>
                  A plan sets how many branches, tills and staff are included.
                  Reaching a ceiling stops you adding another one; it never
                  touches what you already have.
                </p>
                <div className={styles.scroll}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>Plan</th>
                        <th className={styles.num}>Per month</th>
                        <th className={styles.num}>Branches</th>
                        <th className={styles.num}>Tills</th>
                        <th className={styles.num}>Staff</th>
                      </tr>
                    </thead>
                    <tbody>
                      {plans.map((plan) => (
                        <tr key={plan.id}>
                          <td>
                            <div>{plan.name}</div>
                            {plan.description ? (
                              <div className={styles.meta}>
                                {plan.description}
                              </div>
                            ) : null}
                          </td>
                          <td className={styles.num}>
                            {ksh(plan.monthly_price)}
                          </td>
                          <td className={styles.num}>
                            {ceiling(plan.branch_limit)}
                          </td>
                          <td className={styles.num}>
                            {ceiling(plan.register_limit)}
                          </td>
                          <td className={styles.num}>
                            {ceiling(plan.staff_limit)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {history.length > 0 ? (
              <section className={styles.panel}>
                <h2 className={styles.panelTitle}>History</h2>
                <p className={styles.panelLede}>
                  Append-only. A correction is another entry, never an edit to
                  one — a record of a commercial agreement that can be
                  rewritten is not a record.
                </p>
                <div className={styles.scroll}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>What</th>
                        <th>Who</th>
                        <th>When</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((event) => (
                        <tr key={event.id}>
                          <td>
                            <div>{event.kind_label}</div>
                            {event.note ? (
                              <div className={styles.meta}>{event.note}</div>
                            ) : null}
                          </td>
                          <td>{event.actor_name}</td>
                          <td>{when(event.at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}
          </>
        )}

        <p className={styles.footnote}>
          Genmars does not hold a payment method for this business inside the
          POS. Nothing here can charge you, and nothing here stores a card.
        </p>
      </div>
    </Shell>
  );
}
