"""
What a commercial state permits — in one place, like `identity/access.py`.

═══════════════════════════════════════════════════════════════════════════════
SELLING NEVER STOPS. NOT WHEN THE TRIAL ENDS, NOT WHEN THE INVOICE IS LATE,
NOT WHEN THE SUBSCRIPTION IS SUSPENDED.

A till that refuses to ring up a sale because a payment is overdue is a shop
that cannot trade, with a queue at the counter and no way out of it from
behind the till. The damage lands on the cashier and the customer, neither of
whom has any part in the arrangement, and it lands at the worst moment because
a shop is busiest exactly when it can least afford to stop.

This application already decided this once, in another costume:
`StaffCredential.must_change_password` is ASKED and not required, because "a
POS that will not open because somebody cannot think of a password at seven in
the morning is a shop that cannot sell, and refusing to trade is the worse
failure". Non-payment is a commercial problem between Genmars and the business
owner. It is not a reason to stand between a cashier and a customer.

So nothing here can stop a sale, a refund, a shift, a stock movement, a
delivery, or a receipt. What narrows is GROWTH: adding another branch, another
till, another member of staff. Those are decisions the owner makes at a desk,
they are the things a plan is actually sold by, and refusing one costs the
shop nothing it is doing today.
═══════════════════════════════════════════════════════════════════════════════

── TWO REASONS A GROWTH ACTION CAN BE REFUSED, AND THEY READ DIFFERENTLY ───────

  SUSPENDED   nothing has been paid and the grace has run out
  AT LIMIT    the plan is being honoured; it just does not include another one

The second is an upsell and the first is a debt, and a screen that says
"upgrade your plan" to somebody who simply has not paid sends them to the
wrong place. Every refusal here names which it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import State, Subscription

# The things a plan is sold by. Everything absent from this list is operating
# the business, and nothing in this module may touch it.
BRANCH = "branch"
REGISTER = "register"
STAFF = "staff"

GROWTH = (BRANCH, REGISTER, STAFF)

_LIMIT_FIELD = {
    BRANCH: "branch_limit",
    REGISTER: "register_limit",
    STAFF: "staff_limit",
}

_WHAT = {
    BRANCH: ("branch", "branches"),
    REGISTER: ("till", "tills"),
    STAFF: ("member of staff", "staff"),
}

# States in which growth is refused. PAST_DUE is deliberately NOT one of them:
# the grace period exists so that a late invoice does not become an
# operational problem, and narrowing anything during it would make the grace
# a formality.
_BLOCKED = frozenset({State.SUSPENDED, State.CANCELLED})


@dataclass(frozen=True)
class Verdict:
    """
    Whether the action may proceed, and — when it may not — why, in words a
    shop owner can act on.

    `reason` is a machine-readable code so a screen can decide where to send
    somebody; `message` is what they read. Both, because a message parsed for
    keywords is a message nobody can reword.
    """

    allowed: bool
    reason: str = ""
    message: str = ""
    limit: int | None = None
    current: int | None = None


ALLOWED = Verdict(allowed=True)


def subscription_for(organization_id: int) -> Subscription | None:
    return (
        Subscription.objects.select_related("plan")
        .filter(organization_id=organization_id)
        .first()
    )


def state_of(organization_id: int) -> str:
    """
    The commercial state of a tenant.

    ── A TENANT WITH NO SUBSCRIPTION ROW IS TREATED AS ACTIVE ──────────────
    Not as suspended. Every organisation that existed before this app did has
    no row, and reading the absence as "unpaid" would narrow every one of
    them the moment this deploys — turning a new feature into an outage for
    the whole customer base.

    `services.open_trial` gives new tenants a row at onboarding, and
    backfilling the existing ones is a decision for whoever knows what they
    agreed to, not for a default. Until then, no row means no restriction.
    """
    subscription = subscription_for(organization_id)
    return subscription.state() if subscription else State.ACTIVE


def _in_use(organization_id: int, what: str) -> int:
    from branches.models import Branches, Register, staffAssignment
    from organisations.models import OrganizationStaff

    if what == BRANCH:
        return Branches.objects.filter(
            organization_id=organization_id, is_active=True
        ).count()

    if what == REGISTER:
        return Register.objects.filter(
            branch__organization_id=organization_id, is_active=True
        ).count()

    # ── STAFF MEANS PEOPLE CURRENTLY WORKING HERE, NOT ROWS EVER WRITTEN ────
    #
    # `OrganizationStaff` has no active flag; somebody leaves by having their
    # assignments deactivated. Counting rows would mean a shop that has been
    # through thirty cashiers in two years is permanently at the limit of a
    # plan that allows fifteen, with no way to get back under it short of
    # deleting people from an employment record.
    return (
        staffAssignment.objects.filter(
            staff_member__organization_id=organization_id, is_active=True
        )
        .values("staff_member_id")
        .distinct()
        .count()
    )


def may_add(organization_id: int, what: str) -> Verdict:
    """
    May this tenant add one more `what`?

    Refusals are an ordinary commercial answer, not a security one — see
    `limits.py` for why they surface as 402 rather than 403.
    """
    if what not in GROWTH:
        raise ValueError(f"{what!r} is not a growth action; see the banner above")

    singular, plural = _WHAT[what]
    subscription = subscription_for(organization_id)

    if subscription is None:
        return ALLOWED

    state = subscription.state()
    if state in _BLOCKED:
        wording = (
            "This subscription has been cancelled."
            if state == State.CANCELLED
            else "This subscription is suspended for non-payment."
        )
        return Verdict(
            allowed=False,
            reason="subscription_inactive",
            message=(
                f"{wording} Your tills, sales and stock are untouched — only "
                f"adding a new {singular} is on hold until it is settled."
            ),
        )

    plan = subscription.plan
    if plan is None:
        # On trial, or on a bespoke arrangement. Neither has a ceiling to
        # enforce, and inventing one would invent terms nobody agreed.
        return ALLOWED

    limit = getattr(plan, _LIMIT_FIELD[what])
    if limit is None:
        # None is NO CEILING, not a ceiling of zero. See the model.
        return ALLOWED

    current = _in_use(organization_id, what)
    if current < limit:
        return ALLOWED

    return Verdict(
        allowed=False,
        reason="plan_limit",
        message=(
            f"The {plan.name} plan covers {limit} {plural} and you have "
            f"{current}. Moving to a larger plan adds more; nothing you "
            f"already have is affected."
        ),
        limit=limit,
        current=current,
    )


def summary(organization_id: int) -> dict:
    """
    Everything a screen needs to explain the commercial state, in one shape.

    Used by `/auth/me` so a banner can be drawn on any page without a second
    round trip, and by the subscription screen itself.
    """
    subscription = subscription_for(organization_id)
    if subscription is None:
        return {"state": State.ACTIVE, "known": False}

    plan = subscription.plan
    return {
        "known": True,
        "state": subscription.state(),
        "state_label": subscription.get_state_display(),
        "plan": plan.code if plan else None,
        "plan_name": plan.name if plan else None,
        "trial_ends_on": (
            subscription.trial_ends_on.isoformat()
            if subscription.trial_ends_on
            else None
        ),
        "paid_until": (
            subscription.paid_until.isoformat() if subscription.paid_until else None
        ),
        "covered_until": (
            subscription.covered_until.isoformat()
            if subscription.covered_until
            else None
        ),
        "grace_until": (
            subscription.grace_until.isoformat() if subscription.grace_until else None
        ),
        "days_left": subscription.days_left,
        # Said explicitly rather than inferred from the state, because this is
        # the one sentence the banner exists to carry and a client working it
        # out for itself is a client that can get it wrong.
        "selling_continues": True,
        "limits": {
            what: {
                "limit": getattr(plan, _LIMIT_FIELD[what]) if plan else None,
                "in_use": _in_use(organization_id, what),
            }
            for what in GROWTH
        },
    }
