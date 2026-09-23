import Link from "next/link";
import { redirect } from "next/navigation";

import { Mark } from "@/components/Mark";
import { Shell } from "@/components/Shell";
import { progress, type Progress } from "@/lib/onboarding";
import { me as whoAmI, tenantName } from "@/lib/session";
import styles from "./page.module.css";

/**
 * The root route, which is two screens.
 *
 * Signed out it is the front door. Signed in it is the dashboard — empty for
 * now, because step 1 is the shell and the session, not the figures.
 *
 * ── "SIGNED OUT" IS ORDINARY, NOT AN ERROR ─────────────────────────────────
 * /auth/me answers 403 to an anonymous caller, and `getOrNull` turns that into
 * null rather than throwing. Letting it throw here would render an error
 * screen to somebody who has simply not signed in yet, which is the first
 * thing every new visitor would see.
 */

export const dynamic = "force-dynamic";

export default async function Home() {
  const me = await whoAmI();

  if (!me) return <FrontDoor />;

  const state = await progress(me);

  /*
   * ── A SUBSCRIBER WITH NO BUSINESS IS SENT TO MAKE ONE ────────────────────
   *
   * There is nothing for them here and no screen in the application works
   * without a tenant. It is a redirect rather than a rendered prompt so the
   * back button and a bookmarked "/" both behave, and so there is one place
   * that decides this.
   *
   * Only for the business step. Somebody who has a business but no branch yet
   * sees their dashboard, with the remaining steps on it — being nagged into
   * a wizard you have already started is worse than being shown where you got
   * to.
   */
  if (me.kind === "subscriber" && state.next === "business") {
    redirect("/welcome");
  }

  const business = tenantName(me);

  return (
    <Shell me={me}>
      <div className={styles.page}>
        <div className={styles.wrap}>
          <header className={styles.head}>
            <p className={styles.eyebrow}>
              {me.kind === "staff" ? "Branch" : "Organisation"}
            </p>
            <h1 className={styles.title}>{business ?? "Your business"}</h1>
            <p className={styles.sub}>
              Signed in as{" "}
              {me.kind === "subscriber" ? me.email : `${me.name} (${me.username})`}.
            </p>
          </header>

          <Empty state={state} />
        </div>
      </div>
    </Shell>
  );
}

/**
 * What a business with no trading history sees.
 *
 * It says what to do next rather than "no data". This is the first screen
 * every new subscriber lands on, and an empty dashboard that only reports its
 * own emptiness teaches nobody how to change that.
 *
 * ── THE TICKS ARE REAL ─────────────────────────────────────────────────────
 * Derived from what exists, not from a stored step — see lib/onboarding.ts.
 * A checklist that keeps claiming you have no branch after you made one is
 * worse than no checklist, and that is what a stored step becomes the first
 * time somebody adds one through the API.
 */
function Empty({ state }: { state: Progress }) {
  const steps = [
    {
      name: "Your business",
      done: state.business,
      note: "Done — you are signed in to it.",
    },
    {
      name: "A branch",
      done: state.branches > 0,
      note:
        state.branches > 0
          ? `${state.branches} added.`
          : "A physical location. Stock, sales and staff all belong to one.",
    },
    {
      name: "Products, with prices and a tax rule",
      done: state.products > 0,
      note:
        state.products > 0
          ? `${state.products} in the catalogue.`
          : "A shelf price is normally VAT-inclusive here. The rule says so, once, and every sale copies it.",
    },
    {
      name: "A register, and somebody to run it",
      done: state.registers > 0 && state.staff > 0,
      note:
        state.registers > 0 && state.staff === 0
          ? "The till exists. Nobody is set up to work it yet."
          : state.registers > 0
            ? `${state.registers} till${state.registers === 1 ? "" : "s"}, ${state.staff} staff.`
            : "A cashier signs in to the till itself, not through Genmars — their account belongs to your business.",
    },
  ];

  const remaining = steps.filter((s) => !s.done).length;

  return (
    <section className={styles.empty}>
      <h2 className={styles.emptyTitle}>Nothing has been sold yet</h2>
      <p className={styles.emptyBody}>
        Takings, branch comparison and stock alerts appear here once a till has
        taken its first sale.{" "}
        {remaining > 0
          ? `${remaining} thing${remaining === 1 ? "" : "s"} left before one can.`
          : "Everything a sale needs is in place."}
      </p>

      <ol className={styles.steps}>
        {steps.map((step) => (
          <li
            key={step.name}
            className={`${styles.step} ${step.done ? styles.stepDone : ""}`}
          >
            <div>
              <div className={styles.stepName}>{step.name}</div>
              <div className={styles.stepNote}>{step.note}</div>
            </div>
          </li>
        ))}
      </ol>

      {remaining > 0 ? (
        <p className={styles.continueRow}>
          <Link className={styles.button} href="/welcome">
            Continue setting up
          </Link>
        </p>
      ) : null}
    </section>
  );
}

/**
 * The front door.
 *
 * ── GENMARS SPEAKS HERE, BEFORE ANY TENANT EXISTS ──────────────────────────
 * This is the one screen where our mark leads, because there is no business
 * to name yet. Once signed in, the tenant's name takes the heading and the
 * mark shrinks to the sidebar — see Shell.
 */
function FrontDoor() {
  return (
    <div className={styles.door}>
      {/*
        ── THE GRID IS THE ONLY DECORATION, AND IT IS BEHIND EVERYTHING ──────
        A faint ruled ground, drawn in CSS rather than shipped as an image:
        no request, no cache header, nothing to go stale, and it scales to any
        viewport for free. It reads as graph paper — a ledger, which is what
        this is — and it is the one ornamental thing on the page.
      */}
      <div className={styles.doorGrid} aria-hidden="true" />

      <header className={styles.doorBar}>
        <div className={styles.doorBrand}>
          <Mark size={26} />
          <span className={styles.doorMaker}>Genmars</span>
        </div>
        <a className={styles.doorSignIn} href="/auth/start">
          Sign in
        </a>
      </header>

      <main className={styles.doorMain}>
        {/*
          The claim is what the product IS, in the words a shopkeeper would
          use, and the second line is the part they are actually buying. Two
          sentences, one of them coloured — the whole hero.
        */}
        <h1 className={styles.doorTitle}>
          Every branch, every till.
          <span className={styles.doorTitleAccent}>One set of books.</span>
        </h1>

        <p className={styles.doorLede}>
          A point of sale and a back office for a business with more than one
          counter. Ring up a sale, count a drawer, see what every branch took
          — and know where the stock went, because nothing here moves without
          a record of why.
        </p>

        <div className={styles.doorActions}>
          {/*
            A plain <a>, not next/link: /auth/start is Django's, reached
            through Caddy. Routing it client-side would look for a page that
            does not exist in this app.
          */}
          <a className={styles.doorPrimary} href="/auth/start">
            Sign in with Genmars
          </a>
          <a className={styles.doorSecondary} href="/till">
            Open a till
          </a>
        </div>

        {/*
          Three facts rather than three adjectives. Each one is something the
          software does that a reader can go and check, which is the only kind
          of claim worth putting on a front door — Charter 04 §IV.
        */}
        <dl className={styles.doorFacts}>
          <div>
            <dt>Per branch</dt>
            <dd>
              Stock, staff and takings belong to a location, so two shops can
              be compared instead of merged.
            </dd>
          </div>
          <div>
            <dt>Counted, not assumed</dt>
            <dd>
              A till closes by counting the drawer. The difference is recorded
              against the shift, with the cashier&rsquo;s own note beside it.
            </dd>
          </div>
          <div>
            <dt>Nothing moves silently</dt>
            <dd>
              Every change in stock carries the reason for it — a sale, a
              delivery, a breakage — and you can read the whole trail back.
            </dd>
          </div>
        </dl>

        <p className={styles.doorNote}>
          <strong>Cashiers do not sign in here.</strong> Till staff belong to
          the business that employs them and sign in at the register, with
          credentials that never reach Genmars.
        </p>
      </main>
    </div>
  );
}
