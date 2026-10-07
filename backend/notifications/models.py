"""
Telling somebody something happened, without knowing who they are.

═══════════════════════════════════════════════════════════════════════════════
A NOTIFICATION IS ADDRESSED TO AN AUTHORITY, NOT TO A PERSON.

The obvious model has a recipient column. It cannot work here, for two reasons
that are both load-bearing:

  · THERE ARE TWO KINDS OF PRINCIPAL. A subscriber is a `PlatformAccount`; a
    cashier is a `StaffCredential` over an `OrganizationStaff`, and the two
    credential stores must never meet (identity/models.py). A recipient column
    would be two nullable foreign keys on every row, and every query would
    have to remember both.

  · THE RIGHT AUDIENCE CHANGES AFTER THE EVENT. "This order needs approving"
    is for whoever holds `purchasing.approve` at that branch — which is not
    the same set of people this week as last. A row naming a person keeps
    notifying somebody who left and never reaches the person promoted this
    morning, and nothing about it looks wrong.

So a row names a PERMISSION and a SCOPE, and the audience is resolved from live
`identity.access` at read time. The same rule that module states for every
other decision: code asks "may this caller approve a purchase", never "is this
caller a manager".
═══════════════════════════════════════════════════════════════════════════════

── EVENTS, NOT CONDITIONS, WITH ONE DELIBERATE EXCEPTION ──────────────────────

Most of what belongs here is a MOMENT: a payment settled, an order was
submitted, a drawer was counted short. It happened, it is read once, and it is
done.

Low stock is not a moment, it is a STATE, and conflating the two is how a
notification system becomes useless. Raising one per stock movement past the
reorder level means a notification for every sale of a popular item; raising
one and leaving it means "out of stock" sitting there for something restocked
an hour ago. So a stock row is raised on the CROSSING, deduplicated while it is
unresolved, and `resolved_at` is stamped when the quantity goes back above the
level — see `services.stock_level_changed`.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from branches.models import Branches
from identity.models import PlatformAccount
from organisations.models import BusinessOrganization, OrganizationStaff


class Notification(models.Model):
    """One thing worth telling somebody, and who is entitled to hear it."""

    class Kind(models.TextChoices):
        # ── money ──────────────────────────────────────────────────────────
        PAYMENT_CONFIRMED = "payment.confirmed", "Payment confirmed"
        PAYMENT_FAILED = "payment.failed", "Payment failed"
        # ── stock ──────────────────────────────────────────────────────────
        STOCK_LOW = "stock.low", "Stock is low"
        STOCK_OUT = "stock.out", "Out of stock"
        # ── things waiting for a second person ─────────────────────────────
        ORDER_AWAITING = "order.awaiting", "Purchase order awaiting approval"
        COUNT_AWAITING = "count.awaiting", "Stock take awaiting sign-off"
        # ── the day's shape ────────────────────────────────────────────────
        SHIFT_CLOSED = "shift.closed", "Till closed"
        SHIFT_SHORT = "shift.short", "Till closed short"

    class Urgency(models.TextChoices):
        """
        How loudly to draw it. Not a permission and not a filter.

        Three levels and no more. A scale with five becomes a scale where
        everything is a four, and the only question a shop is really asking is
        "do I have to do something about this now, today, or never".
        """

        INFORM = "inform", "Worth knowing"
        ATTEND = "attend", "Needs somebody"
        URGENT = "urgent", "Needs somebody now"

    kind = models.CharField(max_length=24, choices=Kind.choices)
    urgency = models.CharField(
        max_length=8, choices=Urgency.choices, default=Urgency.INFORM
    )

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="notifications"
    )

    # ── THE AUDIENCE ────────────────────────────────────────────────────────
    #
    # `permission` is a name from identity/access.py's catalogue, checked
    # against `access.KNOWN` in the service rather than constrained here: the
    # catalogue is code, a database constraint over it would need a migration
    # every time a permission is added, and the two would disagree the first
    # time somebody forgot.
    permission = models.CharField(
        max_length=32,
        help_text="An identity.access permission name. Who may see this.",
    )

    # ⚠ NULL MEANS THE WHOLE ORGANISATION, NOT "NO BRANCH".
    #
    # The same convention as access.branch_scope, and for the same reason: the
    # two readings are opposites, so one of them has to be written down. A
    # subscription notice concerns the business; a short drawer concerns the
    # branch the till stands in, and a manager at another branch has no
    # business reading it.
    branch = models.ForeignKey(
        Branches,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )

    # ── WHAT IT SAYS ────────────────────────────────────────────────────────
    #
    # Rendered when the notification is RAISED, not when it is read.
    #
    # The alternative — storing ids and composing the sentence at read time —
    # produces a feed that rewrites its own history: "Blue Band is out of
    # stock" becomes "Blue Band 500g (Westlands) is out of stock" because
    # somebody renamed a product, and an entry about a deleted thing either
    # crashes the feed or reads as a blank. A notification is a statement about
    # a moment and the words belong to that moment.
    subject = models.CharField(max_length=120)
    body = models.CharField(max_length=300, blank=True, default="")

    # Where to send somebody who taps it. A path, never a full URL — a stored
    # absolute URL is a stored hostname, and this application is reached on
    # more than one.
    path = models.CharField(max_length=200, blank=True, default="")

    # ── DEDUPLICATION AND RESOLUTION ────────────────────────────────────────
    #
    # `subject_key` identifies the THING a notification is about — an
    # inventory row, a purchase order, a shift — so a standing condition can
    # be raised once and found again. Free-form "app:model:pk" rather than a
    # generic foreign key: a GenericForeignKey cannot be filtered on cheaply
    # and brings a contenttypes join to every feed read, and nothing here ever
    # needs to dereference it.
    subject_key = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # Stamped when the thing the notification described stopped being true:
    # stock came back above its reorder level, an order was approved. A
    # resolved row leaves the feed and stays in the table, because "we were
    # out of sugar for three days last month" is a question somebody asks.
    resolved_at = models.DateTimeField(null=True, blank=True)

    # Who or what caused it, for an audit trail. Both nullable and both
    # optional: a notification raised by a nightly job has neither, and a
    # notification is not an ActivityLog entry — it does not have to prove
    # anything.
    actor_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_caused",
    )
    actor_staff = models.ForeignKey(
        OrganizationStaff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_caused",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The feed query: one organisation, newest first, unresolved.
            models.Index(fields=["organization", "-created_at"]),
            # The dedup lookup: is there already a live one about this thing?
            models.Index(fields=["subject_key", "resolved_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} — {self.subject}"

    @property
    def is_live(self) -> bool:
        return self.resolved_at is None

    def clean(self) -> None:
        if self.branch_id and self.branch.organization_id != self.organization_id:
            raise ValidationError(
                "A notification's branch must belong to its organisation."
            )


class NotificationRead(models.Model):
    """
    That one principal has seen one notification.

    ══════════════════════════════════════════════════════════════════════════
    EXACTLY ONE OF `account` / `staff` IS SET, AND THE DATABASE SAYS SO.

    This is the one table in the application that has to name a principal of
    either tier, because "have you read this" is the one question that is
    genuinely about a person rather than an authority. Two nullable columns
    with a check constraint, rather than a shared abstract "principal" table:
    inventing one would mean a row that can be joined to either credential
    store, which is the thing identity/models.py forbids.

    A read row rather than a `last_read_at` watermark per person, because a
    watermark cannot express "I have dealt with that one and not this older
    one", which is what somebody does with a list of things to act on.
    ══════════════════════════════════════════════════════════════════════════
    """

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="reads"
    )

    account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_reads",
    )
    staff = models.ForeignKey(
        OrganizationStaff,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notification_reads",
    )

    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(account__isnull=False, staff__isnull=True)
                    | models.Q(account__isnull=True, staff__isnull=False)
                ),
                name="read_belongs_to_exactly_one_principal",
            ),
            # Reading twice is not an error and must not be two rows — the
            # unread count is a difference between two counts, and a duplicate
            # would make it negative.
            models.UniqueConstraint(
                fields=["notification", "account"],
                condition=models.Q(account__isnull=False),
                name="one_read_per_notification_per_account",
            ),
            models.UniqueConstraint(
                fields=["notification", "staff"],
                condition=models.Q(staff__isnull=False),
                name="one_read_per_notification_per_staff",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "notification"]),
            models.Index(fields=["staff", "notification"]),
        ]

    def __str__(self) -> str:
        return f"read by {self.account or self.staff}"
