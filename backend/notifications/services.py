"""
Raising a notification, and deciding who may read it.

Views call these and do no reasoning of their own, the same shape
identity/services.py takes.
"""

from __future__ import annotations


from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from identity import access
from identity.authentication import StaffPrincipal
from identity.models import PlatformAccount
from identity.permissions import tenant_scope
from organisations.models import OrganizationStaff

from .models import Notification, NotificationRead


class NotificationError(Exception):
    """A caller asked for something the catalogue cannot express."""


# ═════════════════════════════════════════════════════════════════════════════
# RAISING
# ═════════════════════════════════════════════════════════════════════════════


def raise_notification(
    *,
    kind: str,
    organization_id: int,
    permission: str,
    subject: str,
    body: str = "",
    branch_id: int | None = None,
    urgency: str = Notification.Urgency.INFORM,
    path: str = "",
    subject_key: str = "",
    actor=None,
    dedupe: bool = False,
) -> Notification | None:
    """
    Write one notification. Returns it, or None when `dedupe` suppressed it.

    ⚠ THE PERMISSION IS CHECKED AGAINST THE CATALOGUE, AND THAT MATTERS MORE
      HERE THAN ANYWHERE ELSE.

      `access.may` fails CLOSED on an unknown permission name, which is the
      right direction everywhere else in the codebase: a typo locks somebody
      out of a button and somebody complains. Here it would do the opposite of
      complaining — the notification would be written, addressed to a
      permission nobody holds, and read by NOBODY. A silent audience of zero
      is indistinguishable from a feature that works, because the people who
      would notice are the people not being told.

      So a bad name raises instead. In the one place where failing closed is
      invisible, fail loudly.

    `dedupe` suppresses a second live notification about the same
    `subject_key` and `kind` — for a standing condition like low stock, where
    every sale would otherwise raise another one.
    """
    if permission not in access.KNOWN:
        raise NotificationError(
            f"{permission!r} is not a permission in identity.access.KNOWN. "
            "A notification nobody holds the permission for is read by nobody."
        )
    if kind not in Notification.Kind.values:
        raise NotificationError(f"{kind!r} is not a notification kind.")

    if dedupe:
        if not subject_key:
            raise NotificationError(
                "dedupe needs a subject_key — there is nothing to deduplicate on."
            )
        already = Notification.objects.filter(
            organization_id=organization_id,
            kind=kind,
            subject_key=subject_key,
            resolved_at__isnull=True,
        ).exists()
        if already:
            return None

    account, staff = _as_actor(actor)

    note = Notification(
        kind=kind,
        urgency=urgency,
        organization_id=organization_id,
        permission=permission,
        branch_id=branch_id,
        subject=subject[:120],
        body=body[:300],
        path=path[:200],
        subject_key=subject_key[:64],
        actor_account=account,
        actor_staff=staff,
    )
    note.save()
    return note


def _as_actor(actor) -> tuple[PlatformAccount | None, OrganizationStaff | None]:
    """
    Sort whatever a caller passed into the two columns.

    Three types arrive here and all three are legitimate: a `PlatformAccount`
    (a subscriber, straight off request.user), a `StaffPrincipal` (a till, also
    off request.user) and an `OrganizationStaff` — because the inventory and
    procurement services take an `actor` they have already resolved to a
    personnel record, and asking them to hand over a different type would mean
    changing their signatures for the benefit of a feature they do not care
    about.

    Anything else, including None, is no actor: a notification raised by a
    nightly job has none, which is ordinary.
    """
    if isinstance(actor, PlatformAccount):
        return actor, None
    if isinstance(actor, StaffPrincipal):
        return None, actor.staff
    if isinstance(actor, OrganizationStaff):
        return None, actor
    return None, None


def resolve(*, organization_id: int, kind: str, subject_key: str) -> int:
    """
    Mark every live notification about this thing resolved. Returns how many.

    Called when the thing stopped being true — stock came back above its
    reorder level, an order was approved. A resolved row leaves the feed and
    stays in the table, because "how long were we out of sugar" is a question
    somebody asks later.

    ⚠ AN EMPTY `subject_key` IS REFUSED, AND IT IS NOT A PEDANTIC CHECK.
      `subject_key` is blank by default and most kinds never set one, so an
      empty string here matches EVERY unresolved notification of that kind in
      the organisation — one caller passing a key it failed to build would
      silently clear a shop's whole feed of short drawers. Found by a test that
      did exactly that by accident.
    """
    if not subject_key:
        raise NotificationError(
            "resolve() needs a subject_key. An empty one matches every "
            "notification of that kind in the organisation."
        )

    return Notification.objects.filter(
        organization_id=organization_id,
        kind=kind,
        subject_key=subject_key,
        resolved_at__isnull=True,
    ).update(resolved_at=timezone.now())


# ═════════════════════════════════════════════════════════════════════════════
# READING
# ═════════════════════════════════════════════════════════════════════════════


def _audience_filter(principal) -> Q | None:
    """
    The rows this principal is entitled to, as a Q. None means none at all.

    ══════════════════════════════════════════════════════════════════════════
    THE PERMISSION IS CHECKED PER BRANCH, NOT ONCE FOR THE WHOLE TENANT.

    One person can be a cashier at Westlands and the manager at Karen.
    `access.granted(principal)` with no branch returns the UNION, which would
    show them Karen's short drawer on a Westlands terminal. So the filter is
    built from `permissions_by_branch`: a branch-scoped notification is visible
    only where its permission is held AT that branch.

    An organisation-wide notification (branch is null) is matched against the
    union, which is correct — there is no branch for it to be held at, and the
    authority it names is organisation-wide by §2 in every role that has it.
    ══════════════════════════════════════════════════════════════════════════
    """
    if isinstance(principal, PlatformAccount):
        held = access.granted(principal)
        if not held:
            return None
        organisations = tenant_scope(principal)
        if not organisations:
            return None
        # A subscriber's authority is organisation-wide (§2), so no branch
        # narrows it: every branch of their own organisations is in scope.
        return Q(organization_id__in=organisations, permission__in=held)

    if isinstance(principal, StaffPrincipal):
        organisation = principal.organization_id
        branches = access.branch_scope(principal) or []

        # Organisation-wide rows, against the union of what they hold anywhere.
        union = access.granted(principal)
        wide = (
            Q(organization_id=organisation, branch__isnull=True, permission__in=union)
            if union
            else Q(pk__in=[])
        )

        # Then one clause per branch, with the permissions held THERE.
        narrow = Q(pk__in=[])
        for branch_id in branches:
            here = access.granted(principal, branch_id)
            if not here:
                continue
            narrow |= Q(
                organization_id=organisation,
                branch_id=branch_id,
                permission__in=here,
            )

        combined = wide | narrow
        return combined

    return None


def feed(principal, *, limit: int = 50, include_read: bool = False):
    """
    What this principal should be shown, newest first.

    Resolved notifications are excluded always: the feed is a list of things
    that are still true or still need doing. Read ones are excluded by default
    and asked for explicitly, because the bell is about what is outstanding and
    a history is a different screen.
    """
    audience = _audience_filter(principal)
    if audience is None:
        return Notification.objects.none()

    rows = Notification.objects.filter(audience).filter(resolved_at__isnull=True)

    if not include_read:
        rows = rows.exclude(_read_by(principal))

    return rows.select_related("branch").order_by("-created_at")[:limit]


def _read_by(principal) -> Q:
    """Rows this principal has already read."""
    if isinstance(principal, PlatformAccount):
        return Q(reads__account=principal)
    if isinstance(principal, StaffPrincipal):
        return Q(reads__staff=principal.staff)
    return Q(pk__in=[])


def unread_count(principal) -> int:
    """
    How many are outstanding. The number on the bell.

    Counted rather than len(feed()) so the limit does not cap it — a bell
    showing "50" when there are two hundred is a bell that stops being
    information.
    """
    audience = _audience_filter(principal)
    if audience is None:
        return 0
    return (
        Notification.objects.filter(audience)
        .filter(resolved_at__isnull=True)
        .exclude(_read_by(principal))
        .count()
    )


@transaction.atomic
def mark_read(principal, *, ids: list[int] | None = None) -> int:
    """
    Mark notifications read for this principal. `ids=None` means all of them.

    ⚠ IT ONLY EVER MARKS ROWS THE PRINCIPAL COULD SEE. The ids come from a
      request, so without re-applying the audience filter this would be a way
      to write read rows against another tenant's notifications — harmless in
      itself, and a way to confirm which notification ids exist, which is the
      enumeration oracle `portal/selectors.py` answers 404 to avoid.
    """
    audience = _audience_filter(principal)
    if audience is None:
        return 0

    rows = Notification.objects.filter(audience).filter(resolved_at__isnull=True)
    if ids is not None:
        rows = rows.filter(pk__in=ids)
    rows = rows.exclude(_read_by(principal))

    account = principal if isinstance(principal, PlatformAccount) else None
    staff = principal.staff if isinstance(principal, StaffPrincipal) else None
    if account is None and staff is None:
        return 0

    made = [
        NotificationRead(notification=row, account=account, staff=staff)
        for row in rows
    ]
    if not made:
        return 0

    # ignore_conflicts because two tabs pressing "mark all read" at once is
    # ordinary, and the unique constraints are what make it safe rather than a
    # duplicate that would make the unread count negative.
    NotificationRead.objects.bulk_create(made, ignore_conflicts=True)
    return len(made)


# ═════════════════════════════════════════════════════════════════════════════
# THE EVENTS THEMSELVES
# ═════════════════════════════════════════════════════════════════════════════
#
# One function per thing that happens, called by the service that performs the
# act. They live here rather than inline at the call sites so that the words a
# shop reads are written in one file — a sentence composed at the point of a
# stock movement and another at the point of a sale drift apart, and then two
# notifications about the same thing read differently.
#
# ⚠ EVERY ONE OF THESE MUST BE HARMLESS TO FAIL. A notification is a courtesy;
#   a sale is money. None of them may raise into the caller's transaction —
#   `_safely` is the guard, and the reason it exists is that a NOT NULL
#   violation in a subject line must not roll back somebody's checkout.

import logging

log = logging.getLogger(__name__)


def _safely(what: str, fn, *args, **kwargs):
    """
    Run a raise and swallow anything it throws, loudly.

    ══════════════════════════════════════════════════════════════════════════
    A NOTIFICATION MUST NEVER BE ABLE TO FAIL A SALE.

    These are called from inside `@transaction.atomic` blocks that are doing
    the real work: taking a payment, closing a drawer, receiving a delivery. An
    exception here would roll that back — so a shop would be unable to sell
    because of a bug in the code that tells somebody about selling.

    ⚠ IT LOGS AND DOES NOT RE-RAISE, WHICH MEANS A BROKEN NOTIFICATION IS
      INVISIBLE TO THE PERSON WHO SHOULD HAVE RECEIVED IT. That is the right
      trade in this direction and it is still a cost: the log line is the only
      evidence, so it names the event rather than saying "notification failed".
    ══════════════════════════════════════════════════════════════════════════
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        # exc_info is on: unlike the mail failures elsewhere in this codebase,
        # nothing in these arguments is a credential — a subject line and some
        # ids — so a traceback here leaks nothing and is the only way to find
        # the bug.
        log.exception("could not raise the %s notification", what)
        return None


def stock_level_changed(*, inventory, before, after) -> None:
    """
    A shelf crossed its reorder level, in either direction.

    ══════════════════════════════════════════════════════════════════════════
    CALLED FROM THREE PLACES, BECAUSE STOCK IS WRITTEN IN THREE PLACES.

        sales/services.py       a sale takes stock off the shelf
        procurement/services.py a delivery puts it back
        inventory/services.py   `adjust`, for everything else

    There is no single choke point, and the most important of the three is the
    first: stock runs low BECAUSE OF SELLING. Hooking only `adjust` would have
    produced a low-stock notification for a manual correction and silence for
    the shop actually running out of sugar.

    `test_stock_notifications.py` asserts all three paths raise, which is the
    guard against a fourth writer being added without a fourth call.
    ══════════════════════════════════════════════════════════════════════════

    ── IT IS THE CROSSING THAT IS THE EVENT, NOT THE STATE ─────────────────
    Raising whenever `quantity <= reorder_level` would mean a notification per
    sale of a popular item. So this raises when the level is crossed DOWNWARD
    and resolves when it is crossed back up, and `dedupe` catches the case the
    arithmetic cannot: a shop that was already low when this shipped.
    """
    level = inventory.reorder_level
    if level is None:
        return

    organization_id = inventory.branch.organization_id
    key = f"inventory:{inventory.pk}"
    product = inventory.product.name
    where = inventory.branch.branch_name

    was_fine = before > level
    is_low = after <= level
    is_out = after <= 0

    if is_out:
        # Out of stock supersedes low: the two are different urgencies and a
        # shop that has both rows for one product reads the quieter one.
        _safely(
            "stock.out",
            resolve,
            organization_id=organization_id,
            kind=Notification.Kind.STOCK_LOW,
            subject_key=key,
        )
        _safely(
            "stock.out",
            raise_notification,
            kind=Notification.Kind.STOCK_OUT,
            organization_id=organization_id,
            branch_id=inventory.branch_id,
            permission=access.INVENTORY_VIEW,
            urgency=Notification.Urgency.URGENT,
            subject=f"{product} is out of stock",
            body=f"Nothing left at {where}. It cannot be sold until it is booked in.",
            path="/stock",
            subject_key=key,
            dedupe=True,
        )
        return

    if is_low:
        # Anything coming back above zero but still low stops being "out".
        _safely(
            "stock.low",
            resolve,
            organization_id=organization_id,
            kind=Notification.Kind.STOCK_OUT,
            subject_key=key,
        )
        _safely(
            "stock.low",
            raise_notification,
            kind=Notification.Kind.STOCK_LOW,
            organization_id=organization_id,
            branch_id=inventory.branch_id,
            permission=access.INVENTORY_VIEW,
            urgency=Notification.Urgency.ATTEND,
            subject=f"{product} is down to {after}",
            body=(
                f"At or below the reorder level of {level} at {where}. "
                "Time to order more."
            ),
            path="/stock",
            subject_key=key,
            dedupe=True,
        )
        return

    # Back above the line. Both kinds stop being true, and a feed that kept
    # them would be telling a shop it is out of something it restocked.
    if not was_fine:
        for kind in (Notification.Kind.STOCK_LOW, Notification.Kind.STOCK_OUT):
            _safely(
                "stock resolution",
                resolve,
                organization_id=organization_id,
                kind=kind,
                subject_key=key,
            )


def payment_settled(*, push) -> None:
    """
    An M-Pesa push reached its end, one way or the other.

    ── BOTH OUTCOMES, AND THE FAILURE IS THE ONE THAT MATTERS ──────────────
    A confirmed payment is already visible at the till that asked for it — the
    cashier is watching the screen. The reason this notifies on success too is
    the shift supervisor and the office, who are not watching that screen.

    The failure is the urgent one: a push that failed or expired means a
    customer is standing at a counter believing they have paid, or has walked
    out without paying. Somebody other than the cashier needs to be able to see
    that happened without being told.

    ⚠ IT CARRIES NO PHONE NUMBER AND NO RECEIPT. The amount and the outcome are
      what a manager needs; a customer's number in a notification feed is that
      number in every manager's browser and in whatever they screenshot it to.
      `ActivityLog.detail` carries the same prohibition for the same reason.
    """
    from payments.models import StkPush

    organization_id = push.organization_id
    branch = getattr(push, "branch", None)
    amount = push.amount

    if push.status == StkPush.Status.PAID:
        _safely(
            "payment.confirmed",
            raise_notification,
            kind=Notification.Kind.PAYMENT_CONFIRMED,
            organization_id=organization_id,
            branch_id=getattr(branch, "pk", None),
            permission=access.SALES_VIEW,
            urgency=Notification.Urgency.INFORM,
            subject=f"M-Pesa payment of KSh {amount} confirmed",
            body="The customer's payment went through.",
            path="/sales",
            subject_key=f"stkpush:{push.pk}",
        )
        return

    if push.status in {StkPush.Status.FAILED, StkPush.Status.EXPIRED}:
        _safely(
            "payment.failed",
            raise_notification,
            kind=Notification.Kind.PAYMENT_FAILED,
            organization_id=organization_id,
            branch_id=getattr(branch, "pk", None),
            permission=access.SALES_VIEW,
            urgency=Notification.Urgency.URGENT,
            subject=f"M-Pesa payment of KSh {amount} did not go through",
            # The description Safaricom gave, because "why" is the whole
            # question and a cashier cannot see it after the screen has moved
            # on. It is their text, not a customer's data.
            body=(push.result_description or "No reason was given.")[:300],
            path="/sales",
            subject_key=f"stkpush:{push.pk}",
        )


def order_awaiting_approval(*, order, actor=None) -> None:
    """
    Somebody raised a purchase order and cannot approve their own.

    This is the notification the access model implies. `purchasing.manage` and
    `purchasing.approve` are deliberately held by different people — a clerk
    who could approve their own order could order twelve, receive ten and book
    twelve — and the consequence nobody built until now is that the approver
    had no way to learn an order was waiting. The control was a control that
    depended on somebody remembering to look.
    """
    _safely(
        "order.awaiting",
        raise_notification,
        kind=Notification.Kind.ORDER_AWAITING,
        organization_id=order.organization_id,
        branch_id=getattr(order, "branch_id", None),
        permission=access.PURCHASING_APPROVE,
        urgency=Notification.Urgency.ATTEND,
        subject=f"Purchase order {order.number} is waiting for approval",
        body=(
            f"Raised for {order.supplier.name}. It cannot be sent to the "
            "supplier until somebody approves it."
        ),
        path="/buying",
        subject_key=f"purchaseorder:{order.pk}",
        actor=actor,
        # One live notification per order however many times it is resubmitted.
        dedupe=True,
    )


def order_approved(*, order) -> None:
    """The order is no longer waiting, so the notification stops being true."""
    _safely(
        "order.awaiting resolution",
        resolve,
        organization_id=order.organization_id,
        kind=Notification.Kind.ORDER_AWAITING,
        subject_key=f"purchaseorder:{order.pk}",
    )


def count_fully_counted(*, count, counted: int, total: int, actor=None) -> None:
    """
    Every shelf row in a stock take has a figure against it.

    ══════════════════════════════════════════════════════════════════════════
    THERE IS NO "AWAITING SIGN-OFF" STATE, SO THIS IS DERIVED.

    `StockCount.status` is open, closed or abandoned — counting finishing is
    not a transition, it is simply the moment the last line is recorded. Rather
    than add a state (a product change, and not one this feature should be
    making on the way past), the condition is computed where the last line is
    written.

    It is `dedupe`d on the count, so correcting a figure after finishing does
    not raise a second one — and `count_signed_off` resolves it, so the row
    disappears when somebody acts.
    ══════════════════════════════════════════════════════════════════════════

    ⚠ IT CARRIES NO VARIANCE. The person who closes a count holds
      `inventory.count.close`; the person who counted holds `inventory.count`
      and must not learn what the system expected — the count is BLIND, which
      is the whole control (inventory/services.py, and the banner on the
      counting screen). A notification saying "4 short" would publish the
      expected figure to everybody holding the permission to close, which is
      fine, and would sit in the same feed the counter can read, which is not.
      So this says the count is ready and nothing about what it found.
    """
    if counted < total or total == 0:
        return

    _safely(
        "count.awaiting",
        raise_notification,
        kind=Notification.Kind.COUNT_AWAITING,
        organization_id=count.organization_id,
        branch_id=count.branch_id,
        permission=access.INVENTORY_COUNT_CLOSE,
        urgency=Notification.Urgency.ATTEND,
        subject=f"Stock take {count.number} is fully counted",
        body=(
            f"All {total} lines at {count.branch.branch_name} have a figure "
            "against them. It is waiting for sign-off."
        ),
        path="/stock/counts",
        subject_key=f"stockcount:{count.pk}",
        actor=actor,
        dedupe=True,
    )


def count_signed_off(*, count) -> None:
    """Closed or abandoned — either way it is no longer waiting."""
    _safely(
        "count.awaiting resolution",
        resolve,
        organization_id=count.organization_id,
        kind=Notification.Kind.COUNT_AWAITING,
        subject_key=f"stockcount:{count.pk}",
    )


def shift_closed(*, shift, variance) -> None:
    """
    A till was counted and closed.

    ── A SHORT DRAWER IS A DIFFERENT NOTIFICATION, NOT A LOUDER ONE ────────
    "Till 2 closed" is worth knowing. "Till 2 closed KSh 300 short" is the
    thing a manager wants on the day rather than in a month-end report, which
    is where it currently surfaces — by which time nobody remembers who was on
    the register or what happened.

    Over is its own kind of wrong and is reported as short's opposite rather
    than ignored: a drawer with more in it than it should have is an unrecorded
    sale, a wrong float, or change given wrongly, and all three are worth a
    question.

    Addressed to `reports.branch`, which a branch manager holds and a cashier
    does not — the person who counted the drawer should not be the only one who
    knows what it came to.
    """
    register = getattr(shift, "register", None)
    name = getattr(register, "name", None) or "A till"
    branch = getattr(register, "branch", None)
    organization_id = getattr(branch, "organization_id", None)
    if organization_id is None:
        return

    short = variance is not None and variance < 0
    over = variance is not None and variance > 0

    if short or over:
        amount = abs(variance)
        _safely(
            "shift.short",
            raise_notification,
            kind=Notification.Kind.SHIFT_SHORT,
            organization_id=organization_id,
            branch_id=getattr(branch, "pk", None),
            permission=access.REPORTS_BRANCH,
            urgency=Notification.Urgency.ATTEND,
            subject=(
                f"{name} closed KSh {amount} {'short' if short else 'over'}"
            ),
            body=(
                "The counted cash does not match what the till took. "
                "A note against the shift is how it gets explained."
            ),
            path="/reports",
            subject_key=f"shift:{shift.pk}",
        )
        return

    _safely(
        "shift.closed",
        raise_notification,
        kind=Notification.Kind.SHIFT_CLOSED,
        organization_id=organization_id,
        branch_id=getattr(branch, "pk", None),
        permission=access.REPORTS_BRANCH,
        urgency=Notification.Urgency.INFORM,
        subject=f"{name} closed and the drawer agreed",
        body="The counted cash matched what the till took.",
        path="/reports",
        subject_key=f"shift:{shift.pk}",
    )
