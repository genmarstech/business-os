"""
Whether this tenant is paid up, and what follows from it.

═══════════════════════════════════════════════════════════════════════════════
THIS IS AN ENTITLEMENT, NOT AN INVOICE. THE MONEY LIVES AT GENMARS.

gen-portal already holds `Contract`, `Invoice` and `Offer`, with snapshot
semantics, against a client the company has a commercial relationship with.
`BusinessOrganization.genmars_organisation_id` says so in its own comment: it
"records who we invoice, which is a different question from who may open a
till".

So business-os does NOT grow a second invoicing system. What it holds is the
answer: what is this tenant allowed to do, and until when. Who was billed,
what they were charged, whether they paid and by what means are gen-portal's
questions, and duplicating them here would produce two records of one debt
that eventually disagree — the same failure the single identity boundary and
the single tenant-isolation file exist to prevent.

Nothing in this app takes a payment. See the banner in `services.py`.
═══════════════════════════════════════════════════════════════════════════════

── THE STATE IS DERIVED FROM DATES, NOT STORED ─────────────────────────────────

There is no `status` column, on purpose.

A stored status is only as current as the last job that ran. A subscription
whose period ended at midnight still reads `active` until something wakes up
and changes it, and the thing that wakes up is a scheduled task that can fail
quietly — at which point the database says a lapsed tenant is in good standing
and every check in the application believes it.

So the facts are stored — when the trial ends, what has been paid for, how
long the grace is, whether somebody cancelled — and the state is computed from
them on every read. It cannot go stale, it needs no cron to be correct, and
restoring a backup from last month yields the right answer for today rather
than last month's answer.

── AND `cancelled_on` IS A FACT, WHICH IS WHY IT IS STORED ─────────────────────

Everything else above is arithmetic on a date. A cancellation is a decision
somebody made, and no amount of date arithmetic reproduces it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db import models
from django.utils import timezone

from organisations.models import BusinessOrganization

MONEY = {"max_digits": 12, "decimal_places": 2}
ZERO = Decimal("0.00")


class State(models.TextChoices):
    """
    What a subscription is, right now.

    Not a model field — see the banner. These are the values `Subscription.state()`
    returns, named here so the API, the tests and the frontend agree on the
    spelling.
    """

    TRIALING = "trialing", "On trial"
    ACTIVE = "active", "Paid up"
    # The period has ended and the grace has not. Everything still works; the
    # difference between this and ACTIVE is what the shop is TOLD, not what it
    # may do.
    PAST_DUE = "past_due", "Payment overdue"
    # The grace has run out too. Still not a stop on trading — see
    # entitlement.py, which is emphatic about it.
    SUSPENDED = "suspended", "Suspended"
    CANCELLED = "cancelled", "Cancelled"


class Plan(models.Model):
    """
    What Genmars sells, and the ceilings that come with each one.

    ── PLATFORM DATA. IT HAS NO ORGANISATION, AND THAT IS NOT AN OMISSION ──
    Every other model in this application belongs to a tenant and is scoped
    through `identity/scoping.py`. A plan belongs to Genmars. It must never
    be routed through `TenantScoped`, because there is no organisation column
    for it to filter on and the mixin would fail in a way that reads like a
    bug rather than like a category error.

    Reading one is open to any signed-in caller: somebody deciding whether to
    upgrade has to see what they would be upgrading to. Writing one has NO
    endpoint at all — see `views.py`.

    ── NO PLAN IS SEEDED, AND NO PRICE IS INVENTED HERE ────────────────────
    Charter 04 §IV: nothing untrue on a Genmars surface. The company's
    published prices live in `gen-website/src/lib/company.ts` and are already
    duplicated into `gen-portal`'s `seed_services.py` — CLAUDE.md notes that
    the two currently match and that nothing tests that they still do. A
    third copy, invented by whoever wrote this file, would be a price the
    company never agreed to, displayed to a customer.

    So the table ships empty. Plans are entered by Genmars, and a tenant with
    no plan is on trial or on a bespoke arrangement, both of which are real.
    """

    code = models.SlugField(
        max_length=40,
        unique=True,
        help_text="Stable identifier used in code and in URLs. Never reused.",
    )
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)

    monthly_price = models.DecimalField(**MONEY, default=ZERO)

    # ── NULL MEANS NO CEILING, NOT A CEILING OF ZERO ────────────────────────
    #
    # The same shape as `access.branch_scope` returning None for unrestricted
    # authority, and the same trap: a falsy check reads "unlimited" as "none
    # allowed" and locks the largest customer out of adding a branch. Every
    # caller handles None explicitly; `entitlement.py` is the only one.
    branch_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    staff_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    register_limit = models.PositiveSmallIntegerField(null=True, blank=True)

    is_offered = models.BooleanField(
        default=True,
        help_text=(
            "Whether new subscribers may choose it. A withdrawn plan keeps "
            "working for whoever is already on it — moving somebody off the "
            "terms they agreed to is a commercial decision, not a cleanup."
        ),
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "monthly_price", "name"]

    def __str__(self) -> str:
        return self.name


class Subscription(models.Model):
    """
    One per tenant. What they are entitled to, and until when.

    ── THE DATES, AND WHAT EACH ONE ACTUALLY MEANS ─────────────────────────
    `trial_ends_on`  — the last day of the trial. Inclusive.
    `paid_until`     — the last day covered by money that has arrived.
                       Inclusive. Null means nothing has been paid for yet,
                       which during a trial is the ordinary state.
    `grace_days`     — how long after the later of those two the shop keeps
                       everything before anything narrows.

    All three are inclusive dates rather than timestamps, because a
    subscription period is a human arrangement measured in days and a shop
    three hours east of the server should not lose an afternoon of its
    trial to a timezone.
    """

    organization = models.OneToOneField(
        BusinessOrganization,
        on_delete=models.CASCADE,
        related_name="subscription",
    )

    # PROTECT, not SET_NULL: losing which plan a tenant was on loses the only
    # record of what ceilings applied to them, and the limits are the whole
    # commercial substance of the row.
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subscriptions",
        help_text="Null while on trial, or on a bespoke arrangement.",
    )

    started_on = models.DateField(default=timezone.localdate)
    trial_ends_on = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)

    grace_days = models.PositiveSmallIntegerField(
        default=14,
        help_text=(
            "Days after the period ends before anything narrows. Per "
            "subscription rather than global, so a customer who is reliably "
            "late can be given room without changing it for everybody."
        ),
    )

    cancelled_on = models.DateField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.organization.name} — {self.get_state_display()}"

    # ── THE DERIVATION ──────────────────────────────────────────────────────

    @property
    def covered_until(self) -> date | None:
        """
        The last day the shop is entitled to, by trial or by payment.

        The LATER of the two, not whichever was set most recently. A tenant
        who pays early during a trial has both, and taking the payment date
        alone would shorten the trial they were promised.
        """
        dates = [d for d in (self.trial_ends_on, self.paid_until) if d is not None]
        return max(dates) if dates else None

    @property
    def grace_until(self) -> date | None:
        covered = self.covered_until
        return covered + timedelta(days=self.grace_days) if covered else None

    def state(self, today: date | None = None) -> str:
        """
        What this subscription is, on `today`.

        `today` is injectable so a test can ask about a date without moving
        the clock, and so a report can ask "what will this be at month end".
        """
        today = today or timezone.localdate()

        # A cancellation outranks everything. Somebody decided; no arithmetic
        # overrides a decision.
        if self.cancelled_on and today >= self.cancelled_on:
            return State.CANCELLED

        covered = self.covered_until
        if covered is None:
            # No trial, no payment, not cancelled. A subscription row that
            # says nothing about entitlement entitles nothing — reporting it
            # as ACTIVE because no end date has passed would make the
            # absence of a date the most generous state in the system.
            return State.SUSPENDED

        if today <= covered:
            # Paid beats trialing when both cover today, because what the
            # shop is told should be the better of two true answers.
            if self.paid_until and today <= self.paid_until:
                return State.ACTIVE
            return State.TRIALING

        grace = self.grace_until
        if grace is not None and today <= grace:
            return State.PAST_DUE
        return State.SUSPENDED

    def get_state_display(self, today: date | None = None) -> str:
        return State(self.state(today)).label

    @property
    def days_left(self) -> int | None:
        """
        Days of entitlement remaining, or None when there is no end date.

        Negative once it has lapsed, deliberately: "overdue by 3" and "3 left"
        are the same screen with a different sign, and collapsing the first to
        zero loses how overdue.
        """
        covered = self.covered_until
        if covered is None:
            return None
        return (covered - timezone.localdate()).days


class SubscriptionEvent(models.Model):
    """
    What was done to a subscription, and by whom.

    ── APPEND-ONLY, LIKE gen-portal's ActivityLog ──────────────────────────
    Nothing updates a row after writing it. If an entry is wrong the
    correction is another entry. A subscription is the record of a commercial
    agreement, and an editable history of one is not a history.

    `detail` is free-form and must never receive a payment reference, a card
    number, an M-Pesa receipt or anything else that belongs to the money —
    the money is gen-portal's, and a copy of it here is a copy to keep
    secure, reconcile and eventually explain.
    """

    class Kind(models.TextChoices):
        TRIAL_STARTED = "trial_started", "Trial started"
        PLAN_CHOSEN = "plan_chosen", "Plan chosen"
        EXTENDED = "extended", "Period extended"
        GRACE_CHANGED = "grace_changed", "Grace changed"
        CANCELLED = "cancelled", "Cancelled"
        REINSTATED = "reinstated", "Reinstated"

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="events"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)

    # Who did it, where there was a person. Null for anything the system did
    # on its own — a trial opened during onboarding has no actor, and
    # inventing one would be a false attribution in an append-only record.
    actor_account = models.ForeignKey(
        "identity.PlatformAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_events",
    )

    detail = models.JSONField(default=dict, blank=True)
    note = models.TextField(blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.at:%Y-%m-%d}"
