"""
The only place a subscription is written.

═══════════════════════════════════════════════════════════════════════════════
NOTHING HERE TAKES A PAYMENT, AND NOTHING HERE SHOULD EVER LEARN HOW.

`extend()` records that a period has been paid for. It does not charge a card,
it does not raise an STK push, it does not know what a card is. The money is
gen-portal's: that is where `Invoice` lives, where M-Pesa credentials live, and
where a payment is reconciled against a client.

Two things follow, and both are rules rather than preferences:

  · No payment credential belongs in this application. The moment one does,
    every tenant's till server becomes a place worth breaking into for it.
  · `extend()` is called BY something that has already seen the money. It is
    the record of a decision, not the making of one, and a caller that treats
    it as "collect payment" has put the entitlement before the cash.

CLAUDE.md is explicit that no live STK push has ever been fired from this
codebase and that the first one is the user's to make. Nothing in this file
moves that line.
═══════════════════════════════════════════════════════════════════════════════

Every state change writes a `SubscriptionEvent`. The log is append-only: a
mistake is corrected by another entry, never by editing one, because the
history of a commercial agreement that can be rewritten is not a history.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from identity.models import PlatformAccount

from .models import Plan, State, Subscription, SubscriptionEvent

# Long enough to set up a shop, put a catalogue in and trade through a full
# week twice. Short enough that it is a trial. Overridable per tenant because
# a customer migrating from another system genuinely needs longer, and the
# alternative is somebody editing the constant.
DEFAULT_TRIAL_DAYS = 30


class SubscriptionError(ValidationError):
    """Refused for a commercial reason, not a permission one."""


def _record(subscription, kind, *, actor=None, note="", **detail):
    """
    Append to the log.

    `actor` is null for anything the system did on its own — a trial opened
    during onboarding has no person behind it, and naming one would be a
    false attribution in a record whose whole value is that it is not edited.
    """
    return SubscriptionEvent.objects.create(
        subscription=subscription,
        kind=kind,
        actor_account=actor if isinstance(actor, PlatformAccount) else None,
        detail=detail,
        note=note,
    )


@transaction.atomic
def open_trial(organization, *, days: int = DEFAULT_TRIAL_DAYS, actor=None):
    """
    Give a new tenant a subscription, on trial.

    Idempotent: called from onboarding, which a subscriber can revisit, and
    re-opening a trial for somebody who has since paid would be a free month
    handed out by a page refresh.
    """
    existing = Subscription.objects.filter(organization=organization).first()
    if existing is not None:
        return existing

    today = timezone.localdate()
    subscription = Subscription.objects.create(
        organization=organization,
        started_on=today,
        trial_ends_on=today + timedelta(days=days),
    )
    _record(
        subscription,
        SubscriptionEvent.Kind.TRIAL_STARTED,
        actor=actor,
        days=days,
        ends_on=subscription.trial_ends_on.isoformat(),
    )
    return subscription


@transaction.atomic
def choose_plan(subscription: Subscription, plan: Plan, *, actor=None):
    """
    Put a tenant on a plan.

    Does NOT extend anything. Choosing a plan says which terms apply; paying
    says until when, and the two arrive separately — a tenant can agree terms
    on the 1st and have the money land on the 5th, and collapsing them would
    hand out four free days or refuse four paid ones depending on which way
    the code happened to round.
    """
    if not plan.is_offered and plan != subscription.plan:
        raise SubscriptionError(
            {"plan": "That plan is no longer offered. Ask Genmars for the current ones."}
        )

    was = subscription.plan
    subscription.plan = plan
    subscription.save(update_fields=["plan", "updated_at"])
    _record(
        subscription,
        SubscriptionEvent.Kind.PLAN_CHOSEN,
        actor=actor,
        plan=plan.code,
        previous=was.code if was else None,
    )
    return subscription


@transaction.atomic
def extend(subscription: Subscription, *, until: date, actor=None, note: str = ""):
    """
    Record that the tenant is covered to `until`.

    ── IT ONLY EVER MOVES FORWARD ──────────────────────────────────────────
    A caller passing an earlier date is shortening something already paid
    for, which is not an extension and is almost always a bug in whatever
    computed the date — a month added to the wrong starting point, most
    often. Refused rather than applied, because applying it silently removes
    entitlement a customer has bought.

    Deliberately takes an absolute date rather than a number of months. "A
    month" from the 31st is a question with three defensible answers, and the
    caller — which has an invoice in front of it — knows which period was
    actually paid for.
    """
    if subscription.paid_until and until <= subscription.paid_until:
        raise SubscriptionError(
            {
                "until": (
                    f"Already covered to {subscription.paid_until.isoformat()}. "
                    "Extending to an earlier date would take back time that "
                    "has been paid for."
                )
            }
        )

    was = subscription.paid_until
    subscription.paid_until = until
    subscription.save(update_fields=["paid_until", "updated_at"])
    _record(
        subscription,
        SubscriptionEvent.Kind.EXTENDED,
        actor=actor,
        note=note,
        until=until.isoformat(),
        previous=was.isoformat() if was else None,
    )
    return subscription


@transaction.atomic
def set_grace(subscription: Subscription, days: int, *, actor=None, note: str = ""):
    was = subscription.grace_days
    subscription.grace_days = days
    subscription.save(update_fields=["grace_days", "updated_at"])
    _record(
        subscription,
        SubscriptionEvent.Kind.GRACE_CHANGED,
        actor=actor,
        note=note,
        days=days,
        previous=was,
    )
    return subscription


@transaction.atomic
def cancel(
    subscription: Subscription,
    *,
    on: date | None = None,
    reason: str = "",
    actor=None,
):
    """
    End the arrangement.

    ── IT DEFAULTS TO THE END OF WHAT WAS PAID FOR, NOT TO TODAY ───────────
    A customer who cancels on the 3rd having paid to the 30th keeps the
    month they bought. Cancelling to today would be keeping their money and
    withdrawing the service, which is not a decision software should make on
    anybody's behalf — and an owner clicking Cancel has not agreed to it.

    `on` is there for the case where somebody genuinely means now, and it is
    then an explicit choice by whoever passed it.
    """
    if subscription.cancelled_on:
        raise SubscriptionError({"detail": "That subscription is already cancelled."})

    today = timezone.localdate()
    covered = subscription.covered_until
    effective = on or (
        covered + timedelta(days=1) if covered and covered >= today else today
    )

    subscription.cancelled_on = effective
    subscription.cancellation_reason = reason
    subscription.save(
        update_fields=["cancelled_on", "cancellation_reason", "updated_at"]
    )
    _record(
        subscription,
        SubscriptionEvent.Kind.CANCELLED,
        actor=actor,
        note=reason,
        effective=effective.isoformat(),
    )
    return subscription


@transaction.atomic
def reinstate(subscription: Subscription, *, actor=None, note: str = ""):
    """
    Undo a cancellation.

    The cancellation EVENT stays in the log. Only the field clears — what
    happened, happened, and an append-only record that loses a reversed
    decision is a record of the present dressed as a history.
    """
    if not subscription.cancelled_on:
        raise SubscriptionError({"detail": "That subscription is not cancelled."})

    was = subscription.cancelled_on
    subscription.cancelled_on = None
    subscription.cancellation_reason = ""
    subscription.save(
        update_fields=["cancelled_on", "cancellation_reason", "updated_at"]
    )
    _record(
        subscription,
        SubscriptionEvent.Kind.REINSTATED,
        actor=actor,
        note=note,
        cancelled_on=was.isoformat(),
    )
    return subscription
