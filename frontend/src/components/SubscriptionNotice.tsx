import Link from "next/link";

import { PERM, may, type Me, type SubscriptionSummary } from "@/lib/session";
import styles from "./SubscriptionNotice.module.css";

/**
 * A line across the top when the subscription needs attention.
 *
 * ── IT LEADS WITH WHAT STILL WORKS ────────────────────────────────────────
 * Every message here says, in its first breath, that the tills, sales and
 * stock are untouched — because they are, and because the reader's first
 * thought on seeing a red bar in a shop is that the shop has stopped. A
 * warning that causes somebody to tell a queue of customers they cannot be
 * served has done more damage than the unpaid invoice.
 *
 * ── AND IT SAYS SOMETHING DIFFERENT TO SOMEBODY WHO CANNOT ACT ────────────
 * An owner is shown what to do. A cashier is told the state and nothing
 * else: sending them to a settings page they will be refused is worse than
 * saying nothing, and telling them to pay an invoice is not their job.
 *
 * Silent while things are fine. A banner that is always there is furniture,
 * and furniture is not read on the day it matters.
 */

type Notice = {
  tone: "info" | "warn" | "urgent";
  heading: string;
  body: string;
};

/** Days before the end of a trial that it becomes worth mentioning. */
const TRIAL_WARNING_DAYS = 7;

function noticeFor(
  subscription: SubscriptionSummary,
  canSettle: boolean,
): Notice | null {
  const act = canSettle
    ? "Settle it with Genmars to lift the hold."
    : "Your manager or the business owner can sort this out with Genmars.";

  switch (subscription.state) {
    case "trialing": {
      const left = subscription.days_left;
      if (left === null || left === undefined || left > TRIAL_WARNING_DAYS) {
        return null;
      }
      return {
        tone: "info",
        heading:
          left <= 0
            ? "Your trial ends today"
            : `Your trial ends in ${left} day${left === 1 ? "" : "s"}`,
        body: canSettle
          ? "Nothing stops when it does — choose a plan whenever you are ready."
          : "Nothing stops when it does.",
      };
    }

    case "past_due":
      return {
        tone: "warn",
        heading: "Payment is overdue",
        /*
          Nothing has narrowed yet and the message must not imply otherwise.
          The grace period exists so a late invoice does not become an
          operational problem — see subscriptions/entitlement.py.
        */
        body: `Everything is working normally. ${act}`,
      };

    case "suspended":
      return {
        tone: "urgent",
        heading: "Your tills, sales and stock are working normally",
        /*
          The heading is the reassurance and the detail is the problem, in
          that order and on purpose. Read at a glance in a busy shop, the
          heading is all that lands.
        */
        body: `This subscription is suspended for non-payment. Adding a branch, a till or a member of staff is on hold until it is settled — nothing else has changed. ${act}`,
      };

    case "cancelled":
      return {
        tone: "warn",
        heading: "This subscription has been cancelled",
        body: canSettle
          ? "Your data is here and your tills still work. Reinstate it whenever you want to carry on."
          : "Your tills still work as normal.",
      };

    default:
      return null;
  }
}

export function SubscriptionNotice({ me }: { me: Me }) {
  const subscription = me.subscription;
  // `known: false` means this tenant predates subscriptions and is
  // unrestricted. Not that it is unpaid — warning about it would be a
  // warning the server does not agree with.
  if (!subscription || !subscription.known) return null;

  const canSettle = may(me, PERM.settingsOrganisation);
  const notice = noticeFor(subscription, canSettle);
  if (!notice) return null;

  return (
    <aside
      className={`${styles.notice} ${styles[notice.tone] ?? ""}`}
      role={notice.tone === "info" ? "status" : "alert"}
    >
      <div className={styles.text}>
        <strong className={styles.heading}>{notice.heading}</strong>{" "}
        <span className={styles.body}>{notice.body}</span>
      </div>
      {canSettle ? (
        <Link href="/settings/subscription" className={styles.action}>
          Subscription
        </Link>
      ) : null}
    </aside>
  );
}
