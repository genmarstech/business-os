"""
Who may do what — blueprint §8, made explicit.

═══════════════════════════════════════════════════════════════════════════════
    "Recommended roles include Owner, Organization Admin, Branch Manager,
     Cashier, Inventory Manager and Accountant. Permissions should be EXPLICIT
     and scoped by organization, branch and sometimes register."

Explicit is the word that decides this module's shape. A role is not a
permission; it is a name for a set of them. Code asks "may this caller refund a
sale", never "is this caller a manager" — because the second question has to be
re-answered in every view the day a shop wants its accountants approving
refunds, and one of those views will be missed.
═══════════════════════════════════════════════════════════════════════════════

── TWO ROLE SOURCES, ONE CATALOGUE ─────────────────────────────────────────────

The two-tier identity decided on 2026-09-21 means authority arrives two ways:

  · a SUBSCRIBER holds a `TenantMembership` — owner, org admin, accountant.
    These are organisation-wide by §2: "the organization level owns the
    business-wide configuration and visibility".
  · an OPERATIONAL staff member holds `staffAssignment` rows — cashier, branch
    manager, inventory clerk and so on, each AT A BRANCH.

Both map onto the same permission names below. A view never asks which tier it
is talking to.

── SCOPE IS TWO QUESTIONS, NOT ONE ─────────────────────────────────────────────

"Which organisations?" was already answered by `tenant_scope`. This module adds
"which branches?", which is the part §8 asks for and the part that was missing:
a cashier at Westlands could read Karen's takings, because nothing narrowed
below the tenant.

`branch_scope` returns **None** for a principal whose authority is
organisation-wide, and a list of branch ids for one confined to branches. None
means "not restricted", NOT "restricted to nothing" — the two are opposite and
confusing them either opens everything or closes everything. Every caller of it
in this codebase handles None explicitly.
"""

from __future__ import annotations

from .authentication import StaffPrincipal
from .models import PlatformAccount, TenantMembership

# ── the catalogue ───────────────────────────────────────────────────────────
#
# Written out as constants rather than free strings. A typo in a free string
# is a permission nobody holds, which fails CLOSED and is therefore survivable
# — but it is also invisible until somebody is locked out of their own till,
# and `KNOWN` below turns it into an error at import time instead.

SALES_CHECKOUT = "sales.checkout"
SALES_VIEW = "sales.view"
SALES_VOID = "sales.void"
SALES_REFUND = "sales.refund"
SALES_REPRINT = "sales.reprint"

SHIFT_OPEN = "shift.open"
SHIFT_CLOSE = "shift.close"

INVENTORY_VIEW = "inventory.view"
INVENTORY_ADJUST = "inventory.adjust"
INVENTORY_TRANSFER = "inventory.transfer"

CATALOG_VIEW = "catalog.view"
CATALOG_MANAGE = "catalog.manage"

CUSTOMER_VIEW = "customer.view"
CUSTOMER_MANAGE = "customer.manage"

# Deliberately two permissions, not one. A branch manager reading their own
# branch's numbers is ordinary; reading every branch's numbers is what the
# organisation level is for (§4 vs §5), and a single "reports" permission
# would hand the second to anybody granted the first.
REPORTS_BRANCH = "reports.branch"
REPORTS_ORGANISATION = "reports.organisation"

BRANCH_MANAGE = "branch.manage"
REGISTER_MANAGE = "register.manage"
STAFF_MANAGE = "staff.manage"

# ── SETTINGS IS TWO PERMISSIONS, NOT ONE ────────────────────────────────────
#
# It was one, and the bundle was wrong. Tax configuration and the
# organisation's own identity are both "settings" in a menu and are not the
# same authority:
#
#   · SETTINGS_TAX changes what future receipts charge. It is ordinary
#     operational work — a VAT rate changes, somebody has to enter it — and
#     an org admin who cannot do it has to fetch the owner to run the shop.
#   · SETTINGS_ORGANISATION changes what the business IS: its registered
#     name, and eventually the billing identity every invoice inherits.
#     gen-portal reserves the equivalent to a founder for the same reason,
#     and it is the one an admin should not hold.
#
# Neither is retroactive: TaxRule.rate is copied onto every SaleItem at the
# moment of sale, so changing a rate cannot rewrite what was already charged.
# That is what makes granting the first one safe.
# ── WHO ELSE MAY ADMINISTER THE BUSINESS ────────────────────────────────────
#
# Inviting a subscriber hands somebody organisation-wide authority over another
# company's money. It is deliberately NOT folded into STAFF_MANAGE, which is
# about who may work a till, nor into SETTINGS_ORGANISATION, which is about the
# organisation's own registered identity. Those are different questions and a
# role can reasonably hold one without the other.
#
# Owner only, for the reason _ADMIN already gives: the permission that grants
# every other permission is the narrow one.
MEMBERS_MANAGE = "members.manage"

SETTINGS_TAX = "settings.tax"
SETTINGS_ORGANISATION = "settings.organisation"

KNOWN = frozenset(
    {
        SALES_CHECKOUT, SALES_VIEW, SALES_VOID, SALES_REFUND, SALES_REPRINT,
        SHIFT_OPEN, SHIFT_CLOSE,
        INVENTORY_VIEW, INVENTORY_ADJUST, INVENTORY_TRANSFER,
        CATALOG_VIEW, CATALOG_MANAGE,
        CUSTOMER_VIEW, CUSTOMER_MANAGE,
        REPORTS_BRANCH, REPORTS_ORGANISATION,
        BRANCH_MANAGE, REGISTER_MANAGE, STAFF_MANAGE, MEMBERS_MANAGE,
        SETTINGS_TAX, SETTINGS_ORGANISATION,
    }
)


# ── subscriber roles: organisation-wide ─────────────────────────────────────

_OWNER = KNOWN  # everything, by definition of owning the business

_ADMIN = KNOWN - {
    # An admin runs the business day to day, tax configuration included —
    # see the note on the two settings permissions above.
    #
    # What is withheld is the pair that changes what the organisation IS:
    # who else may administer it, and its own registered identity. Same
    # instinct as gen-portal reserving `can_manage_access` to a founder — the
    # permission that grants every other permission is the narrow one.
    STAFF_MANAGE,
    MEMBERS_MANAGE,
    SETTINGS_ORGANISATION,
}

_ACCOUNTANT = frozenset(
    {
        # Reads the money, touches none of it. An accountant who could void a
        # sale could make a discrepancy disappear instead of explaining it,
        # which is the one thing the role exists to prevent.
        SALES_VIEW,
        SALES_REPRINT,
        REPORTS_BRANCH,
        REPORTS_ORGANISATION,
        CUSTOMER_VIEW,
        INVENTORY_VIEW,
        CATALOG_VIEW,
    }
)

SUBSCRIBER_ROLES = {
    TenantMembership.Role.OWNER: _OWNER,
    TenantMembership.Role.ADMIN: _ADMIN,
    TenantMembership.Role.ACCOUNTANT: _ACCOUNTANT,
}


# ── operational roles: at a branch ──────────────────────────────────────────
#
# Keyed by `staffAssignment.StaffRoles` values. Those codes are two letters in
# the database ("CA", "IC"); they are mapped here rather than renamed, because
# renaming them is a migration over live assignment rows for no gain.

_CASHIER = frozenset(
    {
        SALES_CHECKOUT,
        SALES_VIEW,
        SALES_REPRINT,
        SHIFT_OPEN,
        CUSTOMER_VIEW,
        CATALOG_VIEW,
        INVENTORY_VIEW,
    }
)

# ── WHAT A CASHIER DELIBERATELY DOES NOT GET ────────────────────────────────
#
# Not SALES_VOID and not SALES_REFUND. Blueprint module 6 lists "manager
# approvals" beside cashier access for exactly this reason: voiding a sale and
# refunding one are how a till is emptied by the person standing at it. They
# need a second person, and the cheapest version of a second person is a
# permission the first one does not hold.
#
# Not REPORTS_BRANCH either. A cashier's own shift figures come from
# `register-status`, which is scoped to their branch; the branch's takings are
# a manager's business.

_BRANCH_MANAGER = frozenset(
    {
        SALES_CHECKOUT, SALES_VIEW, SALES_VOID, SALES_REFUND, SALES_REPRINT,
        SHIFT_OPEN, SHIFT_CLOSE,
        INVENTORY_VIEW, INVENTORY_ADJUST, INVENTORY_TRANSFER,
        CATALOG_VIEW,
        CUSTOMER_VIEW, CUSTOMER_MANAGE,
        # Their branch's numbers, never the organisation's — §5 says a branch
        # manager "should see only the data and actions permitted for that
        # branch".
        REPORTS_BRANCH,
        REGISTER_MANAGE,
    }
)

_INVENTORY = frozenset(
    {
        INVENTORY_VIEW, INVENTORY_ADJUST, INVENTORY_TRANSFER,
        CATALOG_VIEW,
    }
)

_FINANCE_CLERK = frozenset(
    {SALES_VIEW, SALES_REPRINT, REPORTS_BRANCH, CUSTOMER_VIEW, CATALOG_VIEW}
)

_AUDITOR = frozenset(
    {
        # Reads everything at the branch and writes nothing at all. The
        # absence of every *_MANAGE and every write permission is the role.
        SALES_VIEW, REPORTS_BRANCH, INVENTORY_VIEW, CATALOG_VIEW, CUSTOMER_VIEW,
    }
)

OPERATIONAL_ROLES = {
    "AM": _BRANCH_MANAGER,      # Assistant manager
    "CA": _CASHIER,             # Cashier
    "SA": _CASHIER,             # Sales associate — a cashier by another name
    "IC": _INVENTORY,           # Inventory clerk
    "PO": _INVENTORY,           # Purchasing officer; procurement is V2
    "FC": _FINANCE_CLERK,       # Finance clerk
    "BA": _AUDITOR,             # Branch auditor
}


# Fails at import rather than at a till. A permission granted by a role but
# missing from KNOWN is a typo that would otherwise be discovered by somebody
# unable to do their job.
for _role, _grants in {**SUBSCRIBER_ROLES, **OPERATIONAL_ROLES}.items():
    _unknown = set(_grants) - KNOWN
    assert not _unknown, f"{_role} grants unknown permissions: {sorted(_unknown)}"


# ── asking the questions ────────────────────────────────────────────────────


def _assignments(principal):
    """This staff member's live assignments, newest first."""
    from branches.models import staffAssignment

    return staffAssignment.objects.filter(
        staff_member=principal.staff, is_active=True
    ).select_related("branch")


def granted(principal, branch_id: int | None = None) -> frozenset[str]:
    """
    Everything this principal may do — at `branch_id` if one is named.

    ── WHY THE BRANCH ARGUMENT EXISTS ──────────────────────────────────────
    One person can be a cashier at Westlands and the manager at Karen. Taking
    the union of their roles and applying it everywhere would make them a
    manager at Westlands too — a quiet escalation nobody granted, arrived at
    by adding two ordinary assignments.

    So a check that knows which branch it concerns passes it, and gets the
    permissions held THERE. Omitting it returns the union, which is the right
    answer for "may this caller reach this endpoint at all" and the wrong one
    for "may they do it here".
    """
    if isinstance(principal, PlatformAccount):
        # Organisation-wide: the branch, if given, does not narrow anything.
        held: set[str] = set()
        for membership in TenantMembership.objects.filter(account=principal):
            held |= set(SUBSCRIBER_ROLES.get(membership.role, frozenset()))
        return frozenset(held)

    if isinstance(principal, StaffPrincipal):
        rows = _assignments(principal)
        if branch_id is not None:
            rows = rows.filter(branch_id=branch_id)
        held = set()
        for row in rows:
            held |= set(OPERATIONAL_ROLES.get(row.staff_assignment, frozenset()))
        return frozenset(held)

    return frozenset()


def may(principal, permission: str, branch_id: int | None = None) -> bool:
    """
    The question every view should be asking.

    An unknown permission name is refused rather than allowed. It cannot
    happen through the constants above, and it can happen through a string
    typed into a view — where failing closed is the only safe direction.
    """
    if permission not in KNOWN:
        return False
    return permission in granted(principal, branch_id)


def branch_scope(principal) -> list[int] | None:
    """
    The branches this principal is confined to, or **None** for none.

    None means UNRESTRICTED WITHIN THE TENANT — an owner, an admin, an
    accountant. An empty list means confined to no branch at all, which is a
    staff member whose assignments have all been deactivated: they keep a
    valid session and can reach nothing, which is the correct answer for
    somebody who has been taken off the rota mid-shift.

    The two are opposites and reading one as the other either opens
    everything or closes everything, so every caller handles None explicitly
    rather than relying on a falsy check.
    """
    if isinstance(principal, PlatformAccount):
        return None
    if isinstance(principal, StaffPrincipal):
        return list(_assignments(principal).values_list("branch_id", flat=True))
    return []


def scoped_to_branch(queryset, principal, field: str = "branch_id"):
    """
    Narrow a queryset to the caller's branches, on top of the tenant scoping
    that `scoped()` has already applied.

    ⚠ NOT A REPLACEMENT FOR `scoped()`. This answers "which branches", and
    says nothing about which organisation — a branch id alone is not tenant
    isolation. Both, in that order, or a staff principal of one shop reaches
    a same-numbered branch in another.
    """
    branches = branch_scope(principal)
    if branches is None:
        return queryset
    if not branches:
        return queryset.none()
    return queryset.filter(**{f"{field}__in": branches})
