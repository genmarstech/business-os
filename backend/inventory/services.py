"""
Changing a quantity, and the record that explains it.

══════════════════════════════════════════════════════════════════════════════
A QUANTITY NEVER MOVES WITHOUT A ROW SAYING WHY.

Stock is the one figure in a shop that cannot be reconstructed from anything
else. A sale can be recomputed from its lines; a bank balance from its
transactions; a shelf count can only be explained by the movements that
produced it. So `BranchInventory.quantity` is never assigned from a request —
it is changed here, inside a transaction, beside a StockMovement and a
StockAdjustment that say who changed it and what they said about it.

sales/services.py does the same for a checkout and a refund, and this exists
so that the OTHER reasons stock moves — a delivery, a breakage, a count that
disagreed with the system — cannot take a shortcut that a sale is not allowed.
══════════════════════════════════════════════════════════════════════════════

── WHY THIS WAS NOT REACHABLE BEFORE ─────────────────────────────────────────

StockAdjustment has `quantity_before` and `quantity_after` and both are
read-only in the serialiser, correctly: a client that could state the "before"
could state a false one. But nothing computed them either, so POSTing an
adjustment could only ever fail. A shop could sell stock down and had no way to
book a delivery back in.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from identity.models import PlatformAccount
from organisations.models import OrganizationStaff

from .models import (
    BranchInventory,
    StockAdjustment,
    StockCount,
    StockCountLine,
    StockMovement,
)

# Which of the movement kinds an adjustment may claim. PURCHASE, SALE and the
# transfer pair are excluded on purpose: those are produced by the operations
# that own them, and letting a manual adjustment label itself SALE would put
# stock movements in the sales trail that no sale explains.
REASONS = {
    "DELIVERY": ("PURCHASE", "Stock arrived"),
    "COUNT": ("ADJUSTMENT", "A count disagreed with the system"),
    "DAMAGE": ("DAMAGE", "Damaged or expired"),
    "THEFT": ("THEFT", "Missing or stolen"),
    "RETURN": ("RETURN", "Returned to a supplier"),
    "OTHER": ("OTHER", "Something else"),
}


def as_quantity(value) -> Decimal:
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"quantity": "That is not a number."}) from None
    if quantity == 0:
        raise ValidationError({"quantity": "Say how much, and which way."})
    return quantity.quantize(Decimal("0.01"))


@transaction.atomic
def stock_product(*, product, branch, quantity="0", note=""):
    """
    Put a product on a branch's shelf for the first time.

    ══════════════════════════════════════════════════════════════════════════
    THE OPENING COUNT IS A DELIVERY, NOT A STARTING VALUE.

    The tempting shortcut is to create the row with the quantity already in it.
    That writes a number nothing explains — the one thing this module exists to
    prevent — and it is the number every later figure is measured against, so
    an unexplained opening balance quietly undermines the whole trail.

    So the row is created empty and the count booked in through `adjust`, which
    writes the movement. A stock take six months later can walk every unit back
    to the day it arrived.
    ══════════════════════════════════════════════════════════════════════════

    Idempotent on (branch, product): a product already stocked here is returned
    untouched rather than topped up, because the caller is "make sure this is
    sellable", not "another delivery arrived".
    """
    existing = BranchInventory.objects.filter(
        branch=branch, product=product
    ).first()
    if existing is not None:
        return existing

    inventory = BranchInventory.objects.create(
        branch=branch, product=product, quantity=Decimal("0"), is_active=True
    )

    opening = as_quantity(quantity) if str(quantity).strip() not in ("", "0") else None
    if opening is not None and opening > 0:
        adjust(
            inventory=inventory,
            delta=opening,
            reason="DELIVERY",
            note=note or "Opening stock",
        )
        inventory.refresh_from_db()

    return inventory


@transaction.atomic
def adjust(*, inventory: BranchInventory, delta: Decimal, reason: str, note: str = ""):
    """
    Move a quantity by `delta` and write the two rows that explain it.

    ⚠ THE ROW IS LOCKED FIRST. Two people counting the same shelf at once
      would otherwise both read the same "before" and the second would write a
      total that silently discards the first. select_for_update is what makes
      the arithmetic here true rather than probable.
    """
    if reason not in REASONS:
        raise ValidationError({"reason": "Choose why the stock is changing."})

    movement_kind, _ = REASONS[reason]

    locked = (
        BranchInventory.objects.select_for_update().get(pk=inventory.pk)
    )
    before = locked.quantity
    after = before + delta

    if after < 0:
        # A shelf cannot hold less than nothing, and a negative that got in
        # here would make every report downstream of it wrong in a way nobody
        # would trace back to this screen.
        raise ValidationError(
            {
                "quantity": (
                    f"There are only {before} to take away. "
                    "Book a delivery first, or count it in."
                )
            }
        )

    locked.quantity = after
    locked.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        inventory=locked,
        movement_type=movement_kind,
        quantity_before=before,
        quantity_after=after,
        reason=note,
    )

    adjustment = StockAdjustment.objects.create(
        inventory=locked,
        adjustment_type="INCREASE" if delta > 0 else "DECREASE",
        quantity_before=before,
        quantity_after=after,
        reason=note or REASONS[reason][1],
    )

    return adjustment


# ═════════════════════════════════════════════════════════════════════════════
# COUNTING THE SHELVES
# ═════════════════════════════════════════════════════════════════════════════


def _next_count_number(organization_id: int) -> int:
    """
    The next number in this organisation's sequence.

    The same four lines as `sales.services._next_number`, and deliberately not
    imported from there for the reason procurement gives: inventory does not
    depend on sales, and neither should learn about the other to share an
    aggregate.
    """
    highest = StockCount.objects.filter(organization_id=organization_id).aggregate(
        top=Max("number")
    )["top"]
    return 1 if highest is None else highest + 1


def _actor(prefix: str, actor) -> dict:
    """
    Attribution, as whichever of the two columns fits.

    ══════════════════════════════════════════════════════════════════════════
    THE ACTOR IS THE AUTHENTICATED PRINCIPAL, NEVER A FIELD IN THE REQUEST.

    It used to be a field in the request — `opened_by`, `counted_by`,
    `closed_by`, each resolved from an UNFILTERED queryset — and that was two
    bugs wearing one coat. Within a shop, a clerk could sign a count in a
    colleague's name. Across shops, the id was just an integer: a count could
    be attributed to another tenant's employee, whose row is PROTECTed by it
    ever afterwards.

    An attribution a client can state is not an attribution. procurement's
    `_actor` is the twin of this; `_acting` in each app's views.py is where
    the principal becomes a row.
    ══════════════════════════════════════════════════════════════════════════

    Unlike procurement's, this refuses an actor it cannot name. Raising a
    draft order attributed to nobody is survivable — a stock take is a
    control, and a control nobody signed is decoration.
    """
    if isinstance(actor, OrganizationStaff):
        return {f"{prefix}_staff": actor}
    if isinstance(actor, PlatformAccount):
        return {f"{prefix}_account": actor}
    raise ValidationError(
        {"detail": "A count has to be signed by somebody. Sign in again."}
    )


@transaction.atomic
def open_count(*, branch, actor, note: str = ""):
    """
    Begin counting a branch.

    Refuses a second open count at the same branch. The database says so too
    — see the partial constraint on StockCount — and this exists so the
    refusal is a sentence rather than an IntegrityError.
    """
    signature = _actor("opened_by", actor)

    existing = StockCount.objects.filter(
        branch=branch, status=StockCount.Status.OPEN
    ).first()
    if existing is not None:
        raise ValidationError(
            {
                "detail": (
                    f"Count {existing.number} is already open at this branch. "
                    "Close or abandon it before starting another."
                )
            }
        )

    return StockCount.objects.create(
        organization_id=branch.organization_id,
        branch=branch,
        number=_next_count_number(branch.organization_id),
        note=(note or "").strip(),
        **signature,
    )


@transaction.atomic
def record_count(*, count, inventory, counted, actor, note: str = ""):
    """
    Write down what is actually on one shelf.

    ⚠ NOTHING MOVES HERE. The line records a disagreement; the stock is not
      corrected until the count closes. That gap is the point: a manager
      reviews the variances together, as a sheet, before anything is written
      off. Applying each line as it was counted would make a stock take a
      stream of silent adjustments nobody ever saw as a whole.
    """
    if not count.is_open:
        raise ValidationError({"detail": "That count is closed."})
    if inventory.branch_id != count.branch_id:
        # Not a 403: this is an invalid line, not a forbidden one, and the
        # distinction matters because the caller can fix one of them.
        raise ValidationError(
            {"inventory": "That product is not stocked at the branch being counted."}
        )

    quantity = as_quantity(counted)
    if quantity < 0:
        raise ValidationError({"counted": "A shelf cannot hold less than nothing."})

    # Read the system figure under a lock, at this instant. See the banner on
    # StockCountLine: the pair has to be true at one moment, and the lock is
    # what stops a sale landing between the read and the write.
    locked = BranchInventory.objects.select_for_update().get(pk=inventory.pk)

    line, _ = StockCountLine.objects.update_or_create(
        count=count,
        inventory=locked,
        defaults={
            "expected_quantity": locked.quantity,
            "counted_quantity": quantity,
            "note": (note or "").strip(),
            # Both columns, so recounting a shelf cannot leave the previous
            # counter's name beside the new figure. `update_or_create`
            # defaults only overwrite what they name.
            "counted_by_staff": None,
            "counted_by_account": None,
            **_actor("counted_by", actor),
        },
    )
    return line


@transaction.atomic
def close_count(*, count, actor):
    """
    Book every variance, and close the count for good.

    ══════════════════════════════════════════════════════════════════════════
    THE DIFFERENCE IS APPLIED THROUGH `adjust`, NOT WRITTEN TO THE QUANTITY.

    Stock only ever moves through a StockMovement — the rule the whole of this
    module exists to hold. A stock take is the single operation most tempted
    to break it, because it already knows the answer it wants the quantity to
    be, and a count that set `quantity = counted` directly would leave the
    one movement nobody can explain afterwards sitting in the middle of the
    trail every other movement was written to preserve.

    So each line with a variance books a delta, with reason COUNT, exactly as
    a manager correcting one shelf by hand would.
    ══════════════════════════════════════════════════════════════════════════

    The delta is `counted − expected` as recorded on the LINE, not against
    whatever the quantity is now. Sales made since that line was counted are
    real and have already moved the stock; re-deriving the difference here
    would undo them.
    """
    # Resolved first, before a single movement is written. Refusing an
    # unsignable close half way through would leave some variances booked and
    # the count still open — the one state this whole module is built to
    # prevent.
    signature = _actor("closed_by", actor)

    locked = StockCount.objects.select_for_update().get(pk=count.pk)
    if locked.status != StockCount.Status.OPEN:
        raise ValidationError({"detail": "That count has already been closed."})

    applied = 0
    for line in locked.lines.select_related("inventory").all():
        delta = line.counted_quantity - line.expected_quantity
        if delta == 0:
            continue
        adjust(
            inventory=line.inventory,
            delta=delta,
            reason="COUNT",
            note=f"Count {locked.number}" + (f": {line.note}" if line.note else ""),
        )
        # The movement `adjust` just wrote, linked so a line can be traced to
        # the stock it moved. Latest by id rather than by time: two movements
        # in the same transaction can share a timestamp.
        line.movement = (
            StockMovement.objects.filter(inventory=line.inventory)
            .order_by("-id")
            .first()
        )
        line.save(update_fields=["movement"])
        applied += 1

    locked.status = StockCount.Status.CLOSED
    for field, value in signature.items():
        setattr(locked, field, value)
    locked.closed_at = timezone.now()
    locked.save(
        update_fields=[
            "status", "closed_by_staff", "closed_by_account", "closed_at",
        ]
    )
    return locked, applied


@transaction.atomic
def abandon_count(*, count, actor, reason: str):
    """
    Stop a count without booking anything.

    A reason is required. An abandoned count is the one somebody will ask
    about — half the shop counted and then nothing happened — and "no reason
    given" is the answer that makes them ask again.
    """
    signature = _actor("closed_by", actor)

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why this count is being abandoned."})

    locked = StockCount.objects.select_for_update().get(pk=count.pk)
    if locked.status != StockCount.Status.OPEN:
        raise ValidationError({"detail": "That count is not open."})

    locked.status = StockCount.Status.ABANDONED
    for field, value in signature.items():
        setattr(locked, field, value)
    locked.closed_at = timezone.now()
    locked.note = (locked.note + "\n" if locked.note else "") + f"Abandoned: {reason}"
    locked.save(
        update_fields=[
            "status", "closed_by_staff", "closed_by_account", "closed_at", "note",
        ]
    )
    return locked


def count_summary(count) -> dict:
    """
    The sheet a manager signs off, in one query.

    `counted` is how many lines were entered, never how many products the
    branch stocks. A partial count is the ordinary case — one aisle on a
    Tuesday — and reporting it against the full catalogue would make every
    count look unfinished.

    ══════════════════════════════════════════════════════════════════════════
    THE TWO UNIT FIGURES ARE STRINGS. THEY WERE DECIMALS, AND THAT WAS A BUG.

    This dict goes out through a SerializerMethodField, which means no field
    coerces anything in it — DRF's encoder sees a Decimal and does float(obj).
    So a count short by 0.1 + 0.2 litres reported 0.30000000000000004, and
    every other quantity in this application crossed the wire as a string
    precisely to avoid that. The rule is written out at length in
    frontend/src/lib/money.ts and was being broken by the one endpoint that
    did its own serialising.

    The four counts above stay integers. They are counts of lines, not
    quantities, and an integer survives JSON intact.
    ══════════════════════════════════════════════════════════════════════════
    """
    lines = list(count.lines.select_related("inventory__product").all())
    short = [l for l in lines if l.counted_quantity < l.expected_quantity]
    over = [l for l in lines if l.counted_quantity > l.expected_quantity]
    return {
        "counted": len(lines),
        "agreed": len(lines) - len(short) - len(over),
        "short": len(short),
        "over": len(over),
        "units_short": str(
            sum((l.expected_quantity - l.counted_quantity for l in short), Decimal("0"))
        ),
        "units_over": str(
            sum((l.counted_quantity - l.expected_quantity for l in over), Decimal("0"))
        ),
    }
