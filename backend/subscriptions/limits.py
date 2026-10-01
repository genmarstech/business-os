"""
Refusing a growth action the subscription does not cover.

── 402, NOT 403 ────────────────────────────────────────────────────────────────

403 means "you are not allowed to do this". That is a statement about the
caller's authority, and it is wrong here: an owner has every permission the
system has, and is being refused because of an invoice. Telling them they lack
permission sends them to look at roles, which is the one place the answer is
not.

402 Payment Required is exactly this situation and is otherwise unused in the
application, so a client can branch on it without parsing a message. It leaks
nothing: the caller is being told about their OWN commercial arrangement, which
they are a party to — unlike the 404-not-403 rule in `identity/scoping.py`,
which is about never confirming that somebody else's row exists.

── AND THE REFUSAL SAYS WHAT STILL WORKS ───────────────────────────────────────

Every message carries the sentence that the tills, sales and stock are
untouched. A shop owner who hits this at nine in the morning needs to know, in
the same breath, that their shop is not about to stop — see the banner in
`entitlement.py`, which is the whole argument.
"""

from __future__ import annotations

from rest_framework.exceptions import APIException

from identity.scoping import organisations_referenced

from . import entitlement


class PaymentRequired(APIException):
    status_code = 402
    default_detail = "This needs an active subscription."
    default_code = "payment_required"


class GrowthLimited:
    """
    Mix into a `TenantScoped` viewset that creates something a plan is sold
    by.

        class BranchesViewSets(GrowthLimited, TenantScoped, ModelViewSet):
            grows = entitlement.BRANCH

    ⚠ IT MUST COME BEFORE `TenantScoped` IN THE BASES. The hook it implements
    is called from `TenantScoped.create`, so Python has to find this class's
    `check_growth` first — the other order silently keeps the no-op, and the
    limit stops applying with nothing to show for it.
    """

    grows: str = ""

    def adds_one(self, validated_data: dict) -> bool:
        """
        Does this write actually consume a seat?

        True by default. Overridden where a create is really a MOVE —
        assigning somebody who already works here deactivates their previous
        assignment, so the headcount does not change and refusing it at the
        limit would strand a shop that is merely rearranging the staff it
        already pays for.
        """
        return True

    def check_growth(self, validated_data: dict) -> None:
        if not self.grows:
            raise ValueError(
                f"{type(self).__name__} mixes in GrowthLimited and declares no "
                "`grows`. Name what it adds, or drop the mixin."
            )

        # ── THE ORGANISATION COMES FROM THE WRITE, NOT FROM THE CALLER ──────
        #
        # `organisations_referenced` is the same resolver tenant isolation
        # uses, so a Register create reaches its organisation through its
        # branch and a Branch create through its own column, with no second
        # mapping to keep in step with the first.
        #
        # By the time this runs, `refuse_out_of_scope` has already rejected
        # anything outside the caller's tenants — so these ids are ones they
        # are entitled to be told about.
        if not self.adds_one(validated_data):
            return

        for organisation_id in set(organisations_referenced(validated_data).values()):
            verdict = entitlement.may_add(organisation_id, self.grows)
            if not verdict.allowed:
                raise PaymentRequired(
                    {
                        "detail": verdict.message,
                        "reason": verdict.reason,
                        "limit": verdict.limit,
                        "in_use": verdict.current,
                        # Said in the payload and not only in prose, so a
                        # client cannot render this as "your shop is
                        # suspended" over a working till.
                        "selling_continues": True,
                    }
                )
