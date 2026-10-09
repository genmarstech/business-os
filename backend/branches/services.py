"""
Closing a till, and the count that has to happen first.

══════════════════════════════════════════════════════════════════════════════
A SHIFT IS NOT CLOSED BY SETTING A FIELD. IT IS CLOSED BY COUNTING THE DRAWER.

`RegisterShift` has carried `closing_cash` and `closed_at` since the model was
written and nothing ever set either. Closing was a PATCH that moved `status` to
CLOSED: no close time, no counted amount, and therefore no variance — the one
number a till exists to produce. A shop could run for a year and never once
find out that a drawer was short.

So `status` is no longer writable from a request, and this is the only way a
shift ends.
══════════════════════════════════════════════════════════════════════════════

── THE VARIANCE IS DERIVED, NEVER STORED ─────────────────────────────────────

Expected cash is the opening float plus cash taken less change given, all of it
read from COMPLETED sales — rows that blueprint §10 makes immutable. So the
expected figure for a shift is the same today as it will be at an audit in two
years, and storing it would only create a second number that can drift from the
sales it came from.

What IS stored is the count, because a human read it off a drawer and nothing
else can reproduce it.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from notifications import services as notifications

from .models import CashMovement

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Two places, always. Same rounding as sales/services.py."""
    from decimal import ROUND_HALF_UP

    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def as_money(value) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"counted_cash": "That is not an amount."}) from None
    if amount < 0:
        raise ValidationError({"counted_cash": "A drawer cannot hold less than nothing."})
    return amount.quantize(Decimal("0.01"))


def drawer(shift) -> dict:
    """
    What this shift's drawer should hold, and what it was counted at.

    ⚠ ONE IMPLEMENTATION, SHARED WITH THE REPORT.

    sales/reports.register_status answers the same question for an open till.
    Two copies of "opening + cash taken − change given" is two places for a
    shop's end-of-day figure to disagree with its dashboard, and the one that
    is wrong is whichever the manager is not looking at.
    """
    from sales.models import Payment, Sale

    taken = Payment.objects.filter(
        sale__shift=shift,
        sale__status=Sale.Status.COMPLETED,
        method=Payment.Method.CASH,
    ).aggregate(cash=Sum("amount"), change=Sum("change_given"))

    # Quantised, every one of them. Sum() hands back whatever the column
    # gave it, so an aggregate of one 100.00 row arrives as Decimal("100")
    # and reaches a screen showing takings as "100" beside "1,000.00".
    # ── CASH THAT MOVED DURING THE SHIFT ────────────────────────────────────
    #
    # Without these the expected figure is wrong on any day somebody banked
    # the takings or fetched change, and the till reports a shortfall for the
    # most ordinary thing a cash business does. See CashMovement.
    #
    # Summed in two directions rather than as a signed column, because the
    # sign lives in the KIND — and a report that had to remember which way
    # round a number was is a report that will one day get it backwards.
    moved = CashMovement.objects.filter(shift=shift).aggregate(
        paid_in=Sum("amount", filter=Q(kind__in=CashMovement.INWARD)),
        paid_out=Sum("amount", filter=Q(kind__in=CashMovement.OUTWARD)),
    )

    opening = money(shift.opening_cash or ZERO)
    cash = money(taken["cash"] or ZERO)
    change = money(taken["change"] or ZERO)
    paid_in = money(moved["paid_in"] or ZERO)
    paid_out = money(moved["paid_out"] or ZERO)
    expected = money(opening + cash - change + paid_in - paid_out)

    counted = money(shift.closing_cash) if shift.closing_cash is not None else None
    return {
        "opening_cash": opening,
        "cash_taken": cash,
        "change_given": change,
        # Reported separately rather than folded into `expected_cash`, so a
        # manager looking at a drawer that does not add up can see WHY the
        # expected figure is what it is without opening another screen.
        "paid_in": paid_in,
        "paid_out": paid_out,
        "expected_cash": expected,
        "counted_cash": counted,
        # Positive is over, negative is short. Null until somebody counts —
        # an uncounted drawer has no variance, and reporting 0 for one would
        # be the most misleading number on the screen.
        "variance": money(counted - expected) if counted is not None else None,
    }


@transaction.atomic
@transaction.atomic
def record_cash_movement(*, shift, kind: str, amount, reason: str, actor=None):
    """
    Write one movement of cash in or out of an open drawer.

    ⚠ ONLY WHILE THE SHIFT IS OPEN. A movement recorded against a closed
      shift would change the expected figure AFTER somebody counted against
      it, which rewrites a variance that has already been signed off — the
      one number the whole close exists to produce. Correct a closed shift
      with a note, or do not correct it.

    The caller's PERMISSION is checked in the view, not here, and the split is
    the point: a pay-in needs `shift.open` and a pay-out needs `shift.close`.
    See the banner on CashMovement for why the arithmetic makes them different
    authorities.
    """
    if kind not in CashMovement.Kind.values:
        raise ValidationError({"kind": "Choose what kind of movement this is."})
    if kind not in CashMovement.INWARD and kind not in CashMovement.OUTWARD:
        # Unreachable through the choices above, and here because a new kind
        # added to the enum and to neither set would otherwise count as
        # nothing at all in the expected figure — silently, and only visible
        # as a drawer that will not reconcile.
        raise ValidationError({"kind": f"{kind} has no direction."})

    amount = as_money(amount)
    if amount <= ZERO:
        # The direction is the kind. A negative amount would be a second way
        # to express it, and the two would disagree.
        raise ValidationError({"amount": "How much moved? It must be positive."})

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            {"reason": "Say why this money moved. That is what the row is for."}
        )

    locked = type(shift).objects.select_for_update().get(pk=shift.pk)
    if locked.status != "OPEN":
        raise ValidationError(
            {"detail": "That till is closed. Cash cannot move in or out of it."}
        )

    from identity.models import PlatformAccount
    from organisations.models import OrganizationStaff

    return CashMovement.objects.create(
        shift=locked,
        kind=kind,
        amount=amount,
        reason=reason[:200],
        recorded_by_account=actor if isinstance(actor, PlatformAccount) else None,
        recorded_by_staff=actor if isinstance(actor, OrganizationStaff) else None,
    )


def close_shift(*, shift, counted_cash):
    """
    End a shift against a counted drawer.

    ⚠ CLOSING IS NOT REVERSIBLE HERE, AND THAT IS THE POINT. A shift that could
      be reopened and re-counted is a shift whose variance means nothing — the
      second count is the one that agrees. A miscount is corrected by a note
      against the shift, not by closing it again.
    """
    counted = as_money(counted_cash)

    # Re-read under a lock: two managers closing the same till at once would
    # otherwise both pass the check below and the second would overwrite the
    # first's count with its own.
    locked = type(shift).objects.select_for_update().get(pk=shift.pk)

    if locked.status == "CLOSED":
        raise ValidationError(
            {"detail": "That till has already been closed and counted."}
        )

    locked.closing_cash = counted
    locked.closed_at = timezone.now()
    locked.status = "CLOSED"
    locked.save(update_fields=["closing_cash", "closed_at", "status"])

    # ── THE VARIANCE IS RECOMPUTED, NOT CARRIED ─────────────────────────────
    #
    # `drawer()` is the one place that knows what a till should be holding, and
    # it is read AFTER the close so the figure in the notification is the
    # figure the reports will show. Working it out here from `counted` would be
    # a second implementation of the same arithmetic, and the day they disagree
    # is the day a manager is told one number and shown another.
    counts = drawer(locked)
    notifications.shift_closed(shift=locked, variance=counts.get("variance"))

    return locked
