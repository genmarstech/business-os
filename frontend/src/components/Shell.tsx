import Link from "next/link";

import { BusinessMark } from "./BusinessMark";
import { SubscriptionNotice } from "./SubscriptionNotice";
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
      /*
        The count and the account of it are different questions. "48 milk" is
        on Stock; "it was 50 yesterday, where did two go" is here, and for a
        long time only the first had a screen.
      */
      { href: "/stock/history", label: "Stock history", permission: PERM.inventoryView },
      /*
        Gated on `inventory.count`, not on `inventory.view`. An accountant
        and an auditor can both read stock and neither takes a count; a
        screen whose only content is "you cannot do anything here" is worse
        than no link at all.
      */
      {
        href: "/stock/counts",
        label: "Stock takes",
        permission: PERM.inventoryCount,
      },
      { href: "/catalogue", label: "Catalogue", permission: PERM.catalogView },
      /*
        Separate from Catalogue because it answers a different question.
        Catalogue is "what do we sell and what does it normally cost";
        this is "what are we charging instead, where, and until when" —
        and the second is the one that quietly stops being true.
      */
      {
        href: "/catalogue/prices",
        label: "Price lists",
        permission: PERM.catalogView,
      },
    ],
  },
  {
    /*
      Separate from Inventory on purpose. Stock answers "what is on the
      shelf"; this answers "what have we committed to buy, and from whom" —
      and the people who do the two jobs are deliberately not the same people
      (identity/access.py, the four purchasing permissions).
    */
    title: "Buying",
    items: [
      { href: "/buying", label: "Orders", permission: PERM.purchasingView },
      {
        href: "/buying/suppliers",
        label: "Suppliers",
        permission: PERM.purchasingView,
      },
      /*
        Gated on purchasing.view rather than on a reports permission, and
        that is deliberate: a purchasing officer holds no reporting
        permission at all, and what a supplier costs is the working
        information of the only person whose job is to negotiate it. The
        server agrees — see BuyingReportViewSet.
      */
      {
        href: "/buying/reports",
        label: "Spending",
        permission: PERM.purchasingView,
      },
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
      /*
        The shop being PAID — its own paybill, for its own customers. Not to
        be confused with what the shop pays Genmars, which is a different
        arrangement entirely and does not touch these credentials.
      */
      {
        href: "/settings/mpesa",
        label: "M-Pesa",
        permission: PERM.settingsOrganisation,
      },
      /*
        What the business pays Genmars, and until when. Held at the same
        permission as the business's own registered details: it is the
        owner's arrangement, not the shop's operating data, and an
        accountant who reads the books does not read this.
      */
      {
        href: "/settings/subscription",
        label: "Subscription",
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
          <BusinessMark size={28} />
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

      <main className={styles.main}>
        {/*
          Above the page, not over it. Nothing it says can hide what somebody
          came here to do, and it renders nothing at all while things are
          fine — a banner that is always there is furniture.
        */}
        <SubscriptionNotice me={me} />
        {children}
      </main>
    </div>
  );
}
