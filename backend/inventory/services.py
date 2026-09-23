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

from .models import BranchInventory, StockAdjustment, StockMovement

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
