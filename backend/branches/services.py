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

Expected cash is six terms, and the list has grown twice since it was three:

    opening float
  + cash handed over the counter
  − change handed back
  + cash brought in mid-shift          (CashMovement, inward)
  − cash taken out mid-shift           (CashMovement, outward)
  − returns paid out of the drawer     (Refund, method=cash)

All of it read from COMPLETED rows that blueprint §10 makes immutable. So the
expected figure for a shift is the same today as it will be at an audit in two
years, and storing it would only create a second number that can drift from the
sales it came from.

What IS stored is the count, because a human read it off a drawer and nothing
else can reproduce it.

⚠ EVERY TERM IS ALSO RETURNED SEPARATELY, AND THAT IS NOT DECORATION. The
  screens show all six beside the total, because a manager checks a figure
  they do not believe by adding up what it came from. A term that existed in
  this arithmetic and nowhere on a screen is how the double subtraction
  described in `drawer()` — fixed on 2026-10-10 — survived for as long as it
  did.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from notifications import services as notifications

from .models import CashMovement, Safe, SafeMovement

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
    from sales.models import Payment, Refund, Sale

    # ── `amount` IS ALREADY NET OF CHANGE, AND THIS SUBTRACTED IT TWICE ─────
    #
    # ⚠ THE BUG THAT WAS HERE MADE EVERY DRAWER READ OVER, WHICH IS THE
    #   DIRECTION NOBODY INVESTIGATES.
    #
    #   `checkout` records a 1,000 note against a 700 total as `amount` 700
    #   and `change_given` 300 — its comment says so. This aggregate summed
    #   `amount` and then subtracted `change_given` from it, so that sale
    #   contributed 400 to the expected figure when the drawer was 700
    #   heavier. Expected came out LOW by the whole day's change, and a
    #   drawer counted against it read OVER by the same amount.
    #
    #   Which is why it survived: a till reported as over looks like nothing
    #   is wrong. It also hides theft, pound for pound — a cashier taking 500
    #   from a day that gave 2,000 in change leaves a drawer reading 1,500
    #   over instead of 500 short, and the one number a till exists to
    #   produce says the opposite of what happened.
    #
    #   Every existing test passed because they all tender the exact amount,
    #   which is the one kind of cash sale a shop almost never makes.
    #
    # `tendered` is the field whose own comment says the drawer is reconciled
    # against what was physically handed over, and it is NOT used here: it is
    # nullable, so a row written before it existed would drop out of a Sum()
    # and take its sale's cash with it. `amount + change_given` is the same
    # number by construction and cannot be null.
    taken = Payment.objects.filter(
        sale__shift=shift,
        sale__status=Sale.Status.COMPLETED,
        method=Payment.Method.CASH,
    ).aggregate(kept=Sum("amount"), change=Sum("change_given"))

    # ── MONEY HANDED BACK OVER THE COUNTER ──────────────────────────────────
    #
    # A return paid out of the drawer leaves the drawer holding less, exactly
    # as a safe drop does. Left out, the close reads short by every return
    # taken that day — the same failure as banking the takings, arriving by
    # the same route, and the reason the till could not take a return until
    # this was here.
    #
    # ⚠ `method=cash` AND NOT "every refund against this shift".
    #   An M-Pesa reversal and a credit note both give money back without
    #   anybody opening the drawer. Counting those would make the close read
    #   LONG, which is the same bug facing the other way and harder to spot,
    #   because a drawer with too much in it looks like somebody being
    #   careful. Refunds written before `Refund.method` existed are blank and
    #   so fall outside this filter, which is what keeps already-closed
    #   shifts reading exactly as they did.
    #
    # VOIDED refunds are excluded for the reason completed-only applies to
    # the takings above: a reversal that was itself reversed moved no money.
    given_back = Refund.objects.filter(
        shift=shift,
        status=Refund.Status.COMPLETED,
        method=Payment.Method.CASH,
    ).aggregate(cash=Sum("total"))

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
    change = money(taken["change"] or ZERO)
    # What came across the counter, which is what "Taken" means on every
    # screen that shows it. Reported gross rather than net so that the
    # columns a manager reads add up to the figure beside them: taken less
    # change is the money that stayed, and that is the sum below.
    cash = money((taken["kept"] or ZERO) + change)
    paid_in = money(moved["paid_in"] or ZERO)
    paid_out = money(moved["paid_out"] or ZERO)
    refunded = money(given_back["cash"] or ZERO)
    expected = money(opening + cash - change + paid_in - paid_out - refunded)

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
        # Same argument, and it is the figure a manager asks about first when
        # the takings look thin: cash that went back across the counter.
        "refunded_cash": refunded,
        "expected_cash": expected,
        "counted_cash": counted,
        # Positive is over, negative is short. Null until somebody counts —
        # an uncounted drawer has no variance, and reporting 0 for one would
        # be the most misleading number on the screen.
        "variance": money(counted - expected) if counted is not None else None,
    }


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


# ═══════════════════════════════════════════════════════════════════════════
# THE SAFE — THE OTHER END OF A SAFE_DROP
# ═══════════════════════════════════════════════════════════════════════════


def get_or_create_safe(branch):
    """
    Every branch gets exactly one, made the first time anything asks for it.

    Not created alongside the branch itself: most of this application's own
    tests build a `Branches` row and never touch cash, and a Safe nothing
    ever uses is a row to explain, not a feature.
    """
    safe, _ = Safe.objects.get_or_create(branch=branch)
    return safe


def safe_balance(safe) -> Decimal:
    """What this safe is holding right now, summed from its own ledger."""
    moved = SafeMovement.objects.filter(safe=safe).aggregate(
        received=Sum("amount", filter=Q(kind=SafeMovement.Kind.RECEIVED)),
        banked=Sum("amount", filter=Q(kind=SafeMovement.Kind.BANKED)),
    )
    return money((moved["received"] or ZERO) - (moved["banked"] or ZERO))


@transaction.atomic
def receive_into_safe(*, cash_movement, actor=None):
    """
    Confirm that a SAFE_DROP taken off a till has actually arrived.

    ⚠ ONE DROP, ONE RECEIPT, FOR THE SAME REASON A SALE IS NOT DOUBLE-PAID.
      `SafeMovement.cash_movement` is a OneToOne, so a drop already
      confirmed cannot be confirmed again under a second amount — refused
      by the database itself, not merely by this function failing to call
      it twice.

    The amount received is the amount the drop recorded, never a fresh
    figure entered here. If less cash arrived than left the till, that is a
    loss in transit and belongs in a conversation and a note against the
    shift — not a smaller number quietly written into the safe, which would
    make the till's own drop look wrong instead of the journey between them.
    """
    if cash_movement.kind != CashMovement.Kind.SAFE_DROP:
        raise ValidationError(
            {"detail": "Only a drop to the safe can be received into one."}
        )

    if SafeMovement.objects.filter(cash_movement=cash_movement).exists():
        raise ValidationError(
            {"detail": "This drop has already been received into the safe."}
        )

    branch = cash_movement.shift.register.branch
    safe = get_or_create_safe(branch)

    from identity.models import PlatformAccount
    from organisations.models import OrganizationStaff

    return SafeMovement.objects.create(
        safe=safe,
        kind=SafeMovement.Kind.RECEIVED,
        amount=cash_movement.amount,
        cash_movement=cash_movement,
        recorded_by_account=actor if isinstance(actor, PlatformAccount) else None,
        recorded_by_staff=actor if isinstance(actor, OrganizationStaff) else None,
    )


@transaction.atomic
def bank_from_safe(*, safe, amount, reference, note: str = "", actor=None):
    """
    Cash physically leaves the safe for the bank.

    A reference is required because this is the one movement in the whole
    cash chain the application cannot itself verify: a till is counted
    against its own sales, a drop is confirmed by somebody here — this is
    the step that hands money to an institution with no API into this
    database. The deposit slip number is the only thing left tying the row
    to a receipt somebody could be asked to produce.
    """
    amount = as_money(amount)
    if amount <= ZERO:
        raise ValidationError({"amount": "How much was banked? It must be positive."})

    reference = (reference or "").strip()
    if not reference:
        raise ValidationError(
            {"reference": "Enter the deposit slip or reference number."}
        )

    # Locked for the same reason close_shift locks its shift: two people
    # banking the same safe within the same second must not both read a
    # balance that only one of them is actually taking.
    locked = Safe.objects.select_for_update().get(pk=safe.pk)
    balance = safe_balance(locked)
    if amount > balance:
        raise ValidationError(
            {"amount": f"The safe holds KSh {balance}. It cannot bank more than that."}
        )

    from identity.models import PlatformAccount
    from organisations.models import OrganizationStaff

    movement = SafeMovement.objects.create(
        safe=locked,
        kind=SafeMovement.Kind.BANKED,
        amount=amount,
        reference=reference[:100],
        note=(note or "").strip()[:200],
        recorded_by_account=actor if isinstance(actor, PlatformAccount) else None,
        recorded_by_staff=actor if isinstance(actor, OrganizationStaff) else None,
    )

    notifications.cash_banked(safe=locked, amount=amount, reference=movement.reference)

    return movement
