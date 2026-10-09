import { redirect } from "next/navigation";

import { BusinessMark } from "@/components/BusinessMark";
import { me as whoAmI } from "@/lib/session";
import { SignInForm } from "./SignInForm";
import styles from "./page.module.css";

/**
 * Where operational staff sign in to the office.
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE SECOND DOOR, AND THE PRODUCT HAS ALWAYS NEEDED ONE.
 *
 * A subscriber signs in at Genmars and comes back through /auth/callback. A
 * cashier signs in at the till, which holds a bearer token. Between those two
 * sat three roles with nowhere to go at all — a purchasing officer, a finance
 * clerk and a branch auditor, who do not work a register and have no Genmars
 * account, because they are the CUSTOMER's employees.
 *
 * Every screen they need was already built and already gated: Shell.tsx hides
 * a navigation item the caller has no permission for, and half a dozen pages
 * branch on `me.kind === "staff"`. The only missing piece was a way in.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * Signed in already, this redirects rather than offering a form — a sign-in
 * page that lets a signed-in person sign in again as somebody else, by
 * accident, on a shared office machine, is a way to attribute one person's
 * work to another.
 */

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Sign in",
  description: "Sign in to the office with the username your manager gave you.",
};

export default async function SignInPage() {
  if (await whoAmI()) redirect("/");

  return (
    <div className={styles.page}>
      <main className={styles.card}>
        <div className={styles.brand}>
          <BusinessMark size={26} lockup />
        </div>

        <h1 className={styles.title}>Sign in to the office</h1>
        <p className={styles.lede}>
          Use the username your manager gave you — the same one you would use
          at a till. It belongs to the business you work for, not to Genmars,
          and your password never reaches Genmars.
        </p>

        <SignInForm />

        {/*
          ── THE OTHER TWO DOORS, NAMED ───────────────────────────────────
          Somebody who lands here wrongly has a specific other place to be,
          and saying which is cheaper than letting them fail at this form
          three times. The owner's door is a different mechanism entirely —
          a Genmars account — so "sign in" is not one button with a branch.
        */}
        <div className={styles.others}>
          <p>
            <strong>Do you own the business?</strong> You sign in with your
            Genmars account instead — <a href="/auth/start">continue here</a>.
          </p>
          <p>
            <strong>Working a register?</strong> The till has its own screen at{" "}
            <a href="/till">/till</a>, and it remembers the business number so
            you only type it once.
          </p>
        </div>
      </main>
    </div>
  );
}
