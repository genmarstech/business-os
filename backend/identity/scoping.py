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

from rest_framework import serializers

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

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.user, self.tenant_path)

    def perform_create(self, serializer):
        self.refuse_out_of_scope(serializer.validated_data)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self.refuse_out_of_scope(serializer.validated_data)
        super().perform_update(serializer)

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
