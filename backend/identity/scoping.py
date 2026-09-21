"""
Tenant isolation, in one place.

═══════════════════════════════════════════════════════════════════════════════
BLUEPRINT §8: "Never trust branch_id or organization_id supplied by a client as
proof of authorization. Resolve the user's permitted tenant/branch scope from
authenticated server-side identity and enforce it on every protected query and
mutation."

This module is that sentence, made executable. It exists as ONE file for the
same reason gen-portal keeps `portal/selectors.py` as one file: isolation you
can audit in an afternoon is isolation somebody actually audits.
═══════════════════════════════════════════════════════════════════════════════

── IT GUARDS READS **AND** WRITES, WHICH ARE DIFFERENT PROBLEMS ────────────────

Scoping `get_queryset` stops Shop A reading Shop B's rows. On its own it does
nothing about Shop A *writing into* Shop B: a create that names another
tenant's branch, a product moved to somebody else's catalogue, a staff
assignment pointed at a stranger's employee. Those arrive as perfectly valid
foreign keys and the ORM is delighted to save them.

So every write is checked too, and an out-of-scope reference is reported as
**invalid input** rather than as forbidden. "That branch does not exist" and
"that branch is not yours" are the same sentence to somebody who should not
know the difference — the same reason a read returns empty instead of 403.
"""

from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .access import branch_scope, may, scoped_to_branch
from .permissions import scoped, tenant_scope

# ── how each kind of reference reaches an organisation ──────────────────────
#
# Written out rather than guessed at with introspection. A wrong guess here is
# a silent hole, and a name that is missing from this map is a field nobody has
# thought about — which is exactly what should be noticed in review.
#
# Keys are the names DRF puts in `validated_data`; values take the resolved
# model instance and return the organisation id it belongs to.
REFERENCE_TO_ORGANISATION = {
    "organization": lambda obj: obj.pk,
    "branch": lambda obj: obj.organization_id,
    "register": lambda obj: obj.branch.organization_id,
    "staff": lambda obj: obj.organization_id,
    "staff_member": lambda obj: obj.organization_id,
    "operator": lambda obj: obj.organization_id,
    "category": lambda obj: obj.organization_id,
    "product": lambda obj: obj.organization_id,
    # Stock. `inventory` was missing when the stock models merged in, so a
    # movement or an adjustment naming ANOTHER shop's stock was written
    # without a check — the guard read the row's other fields, found nothing
    # it recognised, and let it through. Found by a test, not by review.
    #
    # That is the failure mode of a map like this: it fails OPEN on a field it
    # has never heard of. A new foreign key to anything tenant-owned has to be
    # added here in the same commit that introduces it.
    "inventory": lambda obj: obj.branch.organization_id,
    "from_branch": lambda obj: obj.organization_id,
    "to_branch": lambda obj: obj.organization_id,
    # Sales. Added in the same commit as the sales app, per the warning
    # above — a foreign key to anything tenant-owned that is not named here
    # is a write the guard waves through.
    "customer": lambda obj: obj.organization_id,
    "cashier": lambda obj: obj.organization_id,
    "processed_by": lambda obj: obj.organization_id,
    "shift": lambda obj: obj.register.branch.organization_id,
    "sale": lambda obj: obj.organization_id,
    "sale_item": lambda obj: obj.sale.organization_id,
    "refund": lambda obj: obj.organization_id,
    "tax_rule": lambda obj: obj.organization_id,
}


def organisations_referenced(validated_data: dict) -> dict[str, int]:
    """
    Every organisation this write would touch, by the field that reaches it.

    Returns a mapping so a refusal can name the offending field, which is the
    difference between a usable error and "something was wrong".
    """
    found: dict[str, int] = {}
    for field, resolve in REFERENCE_TO_ORGANISATION.items():
        value = validated_data.get(field)
        if value is None:
            continue
        try:
            found[field] = resolve(value)
        except AttributeError:
            # The field held something other than the model this map expects.
            # Refusing to guess: an unresolvable reference is treated as out of
            # scope below, because the alternative is letting it through.
            found[field] = -1
    return found


class TenantScoped:
    """
    Mix into a ModelViewSet to confine it to the caller's tenants.

        class BranchesViewSets(TenantScoped, viewsets.ModelViewSet):
            tenant_path = "organization_id"

    `tenant_path` is the ORM path from this model to the organisation id —
    `"organization_id"` when the model holds the key itself,
    `"branch__organization_id"` when it reaches one through a branch. It is
    declared per viewset rather than inferred, because inference that quietly
    fails open is worse than a line of boilerplate.
    """

    tenant_path = "organization_id"

    # ── BRANCH SCOPE — blueprint §8, added with identity/access.py ──────────
    #
    # The ORM path from this model to a branch id, or None when the model has
    # no branch (a product, a tax rule — organisation-level things by §7).
    #
    # Set it and an operational principal sees only the branches they are
    # assigned to; a subscriber is unaffected, because `branch_scope` returns
    # None for organisation-wide authority. Leaving it None on a model that
    # DOES belong to a branch is the silent-hole case: a cashier at Westlands
    # reading Karen's rows, which is what this was added to stop.
    branch_path: str | None = None

    # ── PERMISSIONS, PER ACTION ─────────────────────────────────────────────
    #
    #     permissions = {
    #         "list": access.SALES_VIEW,
    #         "create": access.CATALOG_MANAGE,
    #     }
    #
    # `default_permission` covers any action not named. An action with neither
    # is NOT open — `_permission_for` falls back to the default, and a viewset
    # that declares nothing at all keeps the old behaviour of tenant scoping
    # alone, which is what the pre-permission viewsets relied on.
    permissions: dict[str, str] = {}
    default_permission: str | None = None

    def get_queryset(self):
        rows = scoped(super().get_queryset(), self.request.user, self.tenant_path)
        if self.branch_path:
            rows = scoped_to_branch(rows, self.request.user, self.branch_path)
        return rows

    def _permission_for(self, action: str | None) -> str | None:
        return self.permissions.get(action or "", self.default_permission)

    def initial(self, request, *args, **kwargs):
        """
        Check the named permission before the action runs.

        In `initial` rather than in a permission class because the mapping is
        per action and DRF resolves `permission_classes` once for the view —
        and because this is the same reasoning as the guard living in
        `create()` below: a check a subclass can lose by overriding an
        ordinary method is not a check.
        """
        super().initial(request, *args, **kwargs)
        needed = self._permission_for(getattr(self, "action", None))
        if needed and not may(request.user, needed):
            # ── 403 HERE, UNLIKE EVERYWHERE ELSE IN THIS FILE ──────────────
            # The 404-not-403 rule below is about not confirming that another
            # tenant's row exists. This refusal says nothing about anybody
            # else's data — it tells callers about their OWN authority, which
            # they are entitled to know and cannot learn anything from. A 404
            # here would just make a cashier think the reports page is broken.
            raise PermissionDenied("You do not have permission to do that.")

    # ── THE GUARD RUNS IN create()/update(), NOT IN perform_create() ────────
    #
    # It lived in `perform_create` first, which was a mistake found the moment
    # this mixin met code somebody else had written: the stock viewsets each
    # defined their own `perform_create(self, serializer): serializer.save()`
    # — no-ops that did exactly what DRF already does — and every one of them
    # silently overrode the guard. Four write endpoints were unprotected and
    # nothing said so.
    #
    # A protection a subclass can switch off by defining an ordinary method,
    # without ever mentioning it, is not a protection. So the check happens in
    # the action method, before `perform_create` is reached at all. A subclass
    # may still override `perform_create` for its own reasons — and several
    # legitimately do — without being able to lose the guard by accident.
    #
    # The bodies below are DRF's own, with one line added. Duplicating them is
    # the cost of making the check unskippable, and it is worth paying.

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.refuse_out_of_scope(serializer.validated_data)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.refuse_out_of_scope(serializer.validated_data)
        self.perform_update(serializer)

        if getattr(instance, "_prefetched_objects_cache", None):
            instance._prefetched_objects_cache = {}

        return Response(serializer.data)

    def refuse_out_of_scope(self, validated_data: dict) -> None:
        """
        Refuse a write that reaches into a tenant the caller cannot see.

        Raises a ValidationError rather than PermissionDenied on purpose: a 403
        confirms that the branch or product the caller named is real, which
        hands them a way to enumerate another shop's data one id at a time.
        """
        allowed = tenant_scope(self.request.user)
        for field, organisation_id in organisations_referenced(validated_data).items():
            if organisation_id not in allowed:
                raise serializers.ValidationError(
                    {field: "No such record, or it is not available to you."}
                )
