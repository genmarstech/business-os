"""
What each kind of principal is allowed to do.

── THE RULE THE BLUEPRINT ASKS FOR, IN ONE PLACE ───────────────────────────────

§8 of the blueprint: "Never trust branch_id or organization_id supplied by a
client as proof of authorization. Resolve the user's permitted tenant/branch
scope from authenticated server-side identity."

That is what `tenant_scope` below is for. A view asks it which organisations the
caller may touch, and filters on the answer. A view that reads an organisation
id out of the request body and trusts it has skipped the only thing standing
between one shop and another's takings.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from .authentication import StaffPrincipal
from .models import PlatformAccount, TenantMembership


class IsSubscriber(BasePermission):
    """A Genmars-authenticated subscriber. Not a till."""

    message = "Sign in with your Genmars account to do that."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, PlatformAccount)


class IsStaffMember(BasePermission):
    """A till with a live session. Not a subscriber."""

    message = "Open a shift to do that."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, StaffPrincipal)


class IsTenantMember(BasePermission):
    """Either kind of principal, as long as it belongs to some tenant."""

    message = "You do not have access to this organisation."

    def has_permission(self, request, view) -> bool:
        return bool(tenant_scope(request.user))


class IsKnownPrincipal(BasePermission):
    """
    Either kind of principal, whether or not they belong to a tenant yet.

    ── THE STATE THIS EXISTS FOR ──────────────────────────────────────────
    A subscriber who has just completed the Genmars handoff has a session and
    no TenantMembership, because creating their first business is what gives
    them one. `IsTenantMember` refuses them, which is correct for every
    endpoint that touches tenant data and wrong for the two that do not:

      · /auth/me, which is how a client learns it is dealing with somebody
        who has no business yet — and which issues the CSRF cookie, so being
        refused it means never being able to write anything either.
      · creating the first organisation.

    Gating those on membership is a deadlock: you need a business to be
    allowed to make a business. It shipped that way and made onboarding
    impossible, which nothing caught because every test created the
    membership first.
    """

    message = "Sign in to do that."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, (PlatformAccount, StaffPrincipal))


def tenant_scope(principal) -> list[int]:
    """
    The organisation ids this principal may touch. The only answer to that.

    Empty for anybody unauthenticated, and for a subscriber who has signed in
    but not yet created or joined a tenant — which is a real state, and the one
    the onboarding screen exists to resolve.
    """
    if isinstance(principal, StaffPrincipal):
        # A till belongs to exactly one shop, for the life of its session.
        return [principal.organization_id]

    if isinstance(principal, PlatformAccount):
        return list(
            TenantMembership.objects.filter(account=principal).values_list(
                "organization_id", flat=True
            )
        )

    return []


def scoped(queryset, principal, field: str = "organization_id"):
    """
    Narrow any queryset to what this principal may see.

    Deliberately the only helper of its kind, so that "how does isolation work
    here" has one answer and one place to audit — the same reason gen-portal
    keeps `portal/selectors.py` as a single file.

    A read of somebody else's row must come back EMPTY rather than forbidden.
    A 403 confirms the row exists, which is the same leak wearing a different
    status code.
    """
    scope = tenant_scope(principal)
    if not scope:
        return queryset.none()
    return queryset.filter(**{f"{field}__in": scope})


# ── explicit permissions — blueprint §8 ─────────────────────────────────────
#
# Added with identity/access.py, which holds the catalogue and the role map.
# What lives HERE is only the DRF plumbing: everything about who may do what
# is one file away, so the answer to "what can a cashier do" is read in one
# place rather than assembled from permission classes scattered over an app.


class Requires(BasePermission):
    """
    Gate a view on a named permission.

        class SaleViewSet(...):
            permission_classes = [Requires(access.SALES_VIEW)]

    ── IT ANSWERS "AT ALL", NOT "HERE" ─────────────────────────────────────
    This is the endpoint-level check, and it deliberately asks the
    branch-agnostic question: may this caller do this ANYWHERE they work. A
    cashier at one branch reaching a checkout endpoint is fine; whether they
    may check out at the branch in the request is a different question, asked
    by the view once it knows which branch that is.

    Doing it the other way — trying to guess the branch here — would mean
    reading it out of the request body, which is the exact thing §8 forbids.
    """

    def __init__(self, permission: str, message: str | None = None):
        from . import access

        if permission not in access.KNOWN:
            # At import time, where it is a traceback rather than a lockout.
            raise ValueError(f"unknown permission: {permission!r}")
        self.permission = permission
        self.message = message or "You do not have permission to do that."

    def __call__(self):
        # DRF instantiates whatever is in `permission_classes`. Passing an
        # already-configured instance is the neat way to parameterise one, and
        # this makes an instance callable so DRF's `perm()` finds it ready.
        return self

    def has_permission(self, request, view) -> bool:
        from . import access

        return access.may(request.user, self.permission)
