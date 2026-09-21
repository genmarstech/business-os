import { Mark } from "@/components/Mark";
import { Shell } from "@/components/Shell";
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

          <Empty />
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
 * The steps are hard-coded for now. They become live once branches, registers
 * and the catalogue exist — steps 2 and 3 of the build order.
 */
function Empty() {
  return (
    <section className={styles.empty}>
      <h2 className={styles.emptyTitle}>Nothing has been sold yet</h2>
      <p className={styles.emptyBody}>
        Takings, branch comparison and stock alerts appear here once a till has
        taken its first sale. Four things have to exist before one can.
      </p>

      <ol className={styles.steps}>
        <li className={`${styles.step} ${styles.stepDone}`}>
          <div>
            <div className={styles.stepName}>Your business</div>
            <div className={styles.stepNote}>Done — you are signed in to it.</div>
          </div>
        </li>
        <li className={styles.step}>
          <div>
            <div className={styles.stepName}>A branch</div>
            <div className={styles.stepNote}>
              A physical location. Stock, sales and staff all belong to one.
            </div>
          </div>
        </li>
        <li className={styles.step}>
          <div>
            <div className={styles.stepName}>Products, with prices and a tax rule</div>
            <div className={styles.stepNote}>
              A shelf price is normally VAT-inclusive here. The rule says so,
              once, and every sale copies it.
            </div>
          </div>
        </li>
        <li className={styles.step}>
          <div>
            <div className={styles.stepName}>A register, and somebody to run it</div>
            <div className={styles.stepNote}>
              A cashier signs in to the till itself, not through Genmars —
              their account belongs to your business.
            </div>
          </div>
        </li>
      </ol>
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
      <div className={styles.doorCard}>
        <div className={styles.doorBrand}>
          <Mark size={34} />
          <div>
            <div className={styles.doorName}>Genmars</div>
            <div className={styles.doorPlatform}>Business Platform</div>
          </div>
        </div>

        <div className={styles.doorPanel}>
          <h1 className={styles.title} style={{ fontSize: "1.5rem" }}>
            Branches, stock and tills
          </h1>
          <p className={styles.sub}>
            Sign in with the same Genmars account you use for the client
            portal. There is no separate password here and no sign-up form — if
            you do not have an account yet, you will be asked to make one on
            the way through.
          </p>

          <p style={{ margin: "22px 0 0" }}>
            {/*
              A plain <a>, not next/link: /auth/start is Django's, reached
              through Caddy. Routing it client-side would look for a page that
              does not exist in this app.
            */}
            <a className={styles.button} href="/auth/start">
              Sign in with Genmars
            </a>
          </p>
        </div>

        <div className={styles.note}>
          <strong>Cashiers do not sign in here.</strong> Till staff belong to
          the business that employs them and sign in at the register, with
          credentials that never reach Genmars.
        </div>
      </div>
    </div>
  );
}
