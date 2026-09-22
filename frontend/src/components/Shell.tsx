import Link from "next/link";

import { Mark } from "./Mark";
import { PERM, may, tenantName, type Me } from "@/lib/session";
import styles from "./Shell.module.css";

/**
 * The application frame: who you are, whose business this is, and where you
 * may go.
 *
 * ── NAVIGATION IS HIDDEN; ACTIONS ARE DISABLED ─────────────────────────────
 * A whole section the caller can never reach is clutter forever, so a cashier
 * simply has no Reports link. An ACTION on a screen they can see is different
 * — that gets disabled with a reason, so they learn who to ask instead of
 * learning the feature does not exist. That is the manager-approval workflow
 * §6 asks for, expressed as an interface.
 *
 * None of this is security. Every endpoint re-checks; see the banner on
 * lib/session.ts.
 */

/*
 * ── EVERY ITEM HERE HAS TO LEAD SOMEWHERE ──────────────────────────────────
 * A link in a product's own navigation that 404s reads as a broken product,
 * not as an unfinished one. Customers were listed and had no screen — credit
 * accounts are a later piece of work — so the item is gone until there is one
 * rather than sitting here as a promise.
 */
type Item = { href: string; label: string; permission?: string };
type Group = { title: string; items: Item[] };

const GROUPS: Group[] = [
  {
    title: "Trading",
    items: [
      { href: "/till", label: "Till", permission: PERM.salesCheckout },
      { href: "/sales", label: "Sales", permission: PERM.salesView },
      { href: "/refunds", label: "Refunds", permission: PERM.salesView },
    ],
  },
  {
    title: "Inventory",
    items: [
      { href: "/stock", label: "Stock", permission: PERM.inventoryView },
      { href: "/catalogue", label: "Catalogue", permission: PERM.catalogView },
    ],
  },
  {
    title: "Organisation",
    items: [
      {
        href: "/reports",
        label: "Reports",
        permission: PERM.reportsOrganisation,
      },
      { href: "/branches", label: "Branches", permission: PERM.branchManage },
      { href: "/staff", label: "Staff", permission: PERM.staffManage },
      /*
        Two different lists of people, deliberately two items. "Staff" is who
        works a till; "People" is who administers the business. They are
        separate credential stores that never cross — CLAUDE.md, the two-tier
        rule — and one combined item would imply a cashier could be promoted
        into an administrator.
      */
      { href: "/settings/people", label: "People", permission: PERM.reportsOrganisation },
      { href: "/settings/tax", label: "Tax", permission: PERM.settingsTax },
      {
        href: "/settings/business",
        label: "Business details",
        permission: PERM.settingsOrganisation,
      },
    ],
  },
];

export function Shell({
  me,
  children,
}: {
  me: Me;
  children: React.ReactNode;
}) {
  const business = tenantName(me);

  const groups = GROUPS.map((group) => ({
    ...group,
    items: group.items.filter(
      (item) => !item.permission || may(me, item.permission),
    ),
  })).filter((group) => group.items.length > 0);

  return (
    <div className={styles.shell}>
      <nav className={styles.side}>
        <div className={styles.brand}>
          <Mark size={28} />
          <div>
            {/*
              The tenant's business, not ours. If they have not made one yet
              the app has already sent them to /welcome, so this being blank
              means something is wrong and it says so rather than pretending.
            */}
            <div className={styles.tenant}>{business ?? "No business yet"}</div>
            <div className={styles.platform}>Genmars</div>
          </div>
        </div>

        <div className={styles.nav}>
          {groups.map((group) => (
            <div key={group.title}>
              <div className={styles.group}>{group.title}</div>
              {group.items.map((item) => (
                <Link key={item.href} href={item.href} className={styles.link}>
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </div>

        <div className={styles.foot}>
          <div className={styles.who}>
            {me.kind === "subscriber" ? me.email : `${me.name} · ${me.username}`}
          </div>
        </div>
      </nav>

      <main className={styles.main}>{children}</main>
    </div>
  );
}
