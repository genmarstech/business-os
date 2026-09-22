/**
 * Who is signed in, and what they may do.
 *
 * One call to /auth/me, one shape, one place that understands it. Every screen
 * asks this rather than re-deriving authority from a role name — the backend's
 * identity/access.py is emphatic that a role is a name for a set of
 * permissions and not a permission, and the client has no business disagreeing.
 */

import "server-only";

import { getOrNull } from "./api";

export type Membership = {
  id: number;
  name: string;
  role: "owner" | "admin" | "accountant";
};

export type Subscriber = {
  kind: "subscriber";
  email: string;
  full_name: string;
  organisations: Membership[];
  scope: number[];
  /**
   * ⚠ null means UNRESTRICTED, not none.
   *
   * A subscriber's authority is organisation-wide (§2), so no branch narrows
   * it. An empty array would mean confined to nothing — the opposite. A client
   * that treats null as falsy shows an owner an empty shop, which looks like a
   * data bug and is not one.
   */
  branches: number[] | null;
  permissions: string[];
};

export type Staff = {
  kind: "staff";
  /**
   * Their own OrganizationStaff row.
   *
   * The only way a cashier can learn it — /org/staff/ is held at staff.manage
   * and no operational role holds that. Needed to open a shift and to name
   * themselves on a sale or refund, and it confers nothing: the server pins
   * both to the authenticated principal whatever is sent.
   */
  staff_id: number;
  name: string;
  username: string;
  organisation: { id: number; name: string };
  scope: number[];
  branches: number[] | null;
  permissions: string[];
  /**
   * Permissions held AT each branch, keyed by branch id as a string.
   *
   * ── DRAW FROM THIS, NOT FROM `permissions` ─────────────────────────────
   * One person can be a cashier at Westlands and the manager at Karen.
   * `permissions` is the UNION — it answers "somewhere, yes". At a Westlands
   * till it will happily say she may refund, and the server will refuse her.
   * Any screen that knows which branch it is on uses `may(me, perm, branch)`.
   */
  permissions_by_branch: Record<string, string[]>;
};

export type Me = Subscriber | Staff;

/** The signed-in principal, or null. Null is an ordinary state, not a failure. */
export async function me(): Promise<Me | null> {
  return getOrNull<Me>("/auth/me");
}

/**
 * May this principal do `permission`, at `branchId` if one is given?
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THIS IS FOR DRAWING A SCREEN. IT IS NOT SECURITY.
 *
 * The list came from a server response and a browser can edit anything it is
 * given. Every endpoint checks again — identity/scoping.py and sales/views.py
 * do the enforcing. All this does is avoid offering somebody a button they
 * will be refused, and a 403 on any action still has to be handled, because a
 * permission can be revoked between page load and click.
 * ══════════════════════════════════════════════════════════════════════════
 */
export function may(
  principal: Me | null,
  permission: string,
  branchId?: number,
): boolean {
  if (!principal) return false;

  if (principal.kind === "staff" && branchId !== undefined) {
    const held = principal.permissions_by_branch[String(branchId)];
    return held?.includes(permission) ?? false;
  }

  return principal.permissions.includes(permission);
}

/**
 * The name to put in the sidebar: the TENANT's business, not ours.
 *
 * The Genmars mark sits beside it as the platform. A shop owner showing this
 * to their staff should see their own business named first — Charter 04 §V
 * applies the same instinct to client-owned software.
 *
 * A subscriber can belong to several; this takes the first, which is correct
 * until there is a tenant switcher to take the chosen one.
 */
export function tenantName(principal: Me | null): string | null {
  if (!principal) return null;
  if (principal.kind === "staff") return principal.organisation.name;
  return principal.organisations[0]?.name ?? null;
}

/** Permission names, mirroring identity/access.py. */
export const PERM = {
  salesCheckout: "sales.checkout",
  salesView: "sales.view",
  salesVoid: "sales.void",
  salesRefund: "sales.refund",
  salesReprint: "sales.reprint",
  shiftOpen: "shift.open",
  shiftClose: "shift.close",
  inventoryView: "inventory.view",
  inventoryAdjust: "inventory.adjust",
  inventoryTransfer: "inventory.transfer",
  catalogView: "catalog.view",
  catalogManage: "catalog.manage",
  customerView: "customer.view",
  customerManage: "customer.manage",
  reportsBranch: "reports.branch",
  reportsOrganisation: "reports.organisation",
  branchManage: "branch.manage",
  registerManage: "register.manage",
  staffManage: "staff.manage",
  membersManage: "members.manage",
  settingsTax: "settings.tax",
  settingsOrganisation: "settings.organisation",
} as const;
