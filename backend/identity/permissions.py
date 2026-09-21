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
