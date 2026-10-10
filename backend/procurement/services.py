"""
Raising an order, approving it, and booking in what arrives.

═══════════════════════════════════════════════════════════════════════════════
VIEWS AND SERIALISERS DO NOT WRITE PROCUREMENT DOCUMENTS. THEY CALL THIS.

The same argument as sales/services.py, which this deliberately mirrors. A
delivery is: allocate a number, write the receipt, write a line per product,
raise every stock level, write a StockMovement per line, update the order's
running totals and then its status. Nine writes. If the process dies after the
stock went up and before the receipt was written, the shop has inventory that
no document explains — which is precisely the state inventory/services.py was
built to make impossible.

So the whole of it is one transaction, and there is one implementation.
═══════════════════════════════════════════════════════════════════════════════

── STOCK GOES UP THROUGH A StockMovement, NEVER BY ASSIGNMENT ──────────────────

Blueprint §10. A receipt writes `PURCHASE` movements referencing its own
number, so a stock take six months later walks a unit back to the delivery and
from there to the order and the supplier.

It does NOT also write a StockAdjustment. inventory/services.adjust writes both
because a manual correction has no other document to point at; a delivery has
one, and booking an adjustment beside it would double every delivery in any
report that counts adjustments.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from identity.models import PlatformAccount
from inventory.models import BranchInventory, StockMovement
from inventory.services import stock_product
from organisations.models import BusinessOrganization, OrganizationStaff
from notifications import services as notifications

from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")

# Where each sequence starts. Apart from the sales and refund sequences so that
# a number read aloud over a phone is unambiguous about which document it is.
FIRST_ORDER_NUMBER = 3000
FIRST_RECEIPT_NUMBER = 4000


class ProcurementError(ValidationError):
    """
    Anything that stops a document being written.

    Django's ValidationError, which DRF turns into a 400 once `_refuse` in
    views.py shapes it. The messages are read by somebody standing beside a
    delivery driver, so they say what to do.
    """


def money(value) -> Decimal:
    """Two places, half-up — the same rounding the till uses."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def as_quantity(value, *, field="quantity") -> Decimal:
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ProcurementError({field: "That is not a number."}) from None
    if quantity <= 0:
        raise ProcurementError({field: "Say how many, and more than none."})
    return quantity.quantize(CENTS)


def _hold_the_sequence(organization_id: int) -> None:
    """
    Take the organisation's own row, so two documents cannot be handed the
    same number.

    ── WHY A LOCK AND NOT JUST THE UNIQUE CONSTRAINT ───────────────────────
    `_next_number` reads MAX and adds one. Two orders raised in the same
    second read the same MAX, and the second one dies on the constraint with
    an IntegrityError — a 500 at somebody who did nothing wrong, and a number
    burned out of the sequence.

    Serialising on the organisation row is cheap HERE and would not be at a
    till: a shop raises a handful of orders a day and rings up hundreds of
    sales. sales/services.py accepts the race for that reason; this does not
    have to.
    """
    BusinessOrganization.objects.select_for_update().get(pk=organization_id)


def _next_number(model, organization_id: int, first: int) -> int:
    """
    The next number in this organisation's sequence.

    The same four lines as `sales.services._next_number`, and deliberately not
    imported from there: procurement does not depend on sales, and neither
    should learn about the other to share an aggregate. Call it with the
    organisation held — see above.
    """
    highest = model.objects.filter(organization_id=organization_id).aggregate(
        top=Max("number")
    )["top"]
    return first if highest is None else highest + 1


def _actor(prefix: str, actor) -> dict:
    """
    Attribution, as whichever of the two columns fits.

    An unrecognised actor attributes to nobody rather than guessing. That is
    survivable for an order raised by a script; it would not be for an
    approval, which `approve_order` refuses outright instead.
    """
    if isinstance(actor, OrganizationStaff):
        return {f"{prefix}_staff": actor}
    if isinstance(actor, PlatformAccount):
        return {f"{prefix}_account": actor}
    return {}


# ── raising an order ─────────────────────────────────────────────────────────


@transaction.atomic
def raise_order(
    *,
    branch,
    supplier: Supplier,
    lines: list[dict],
    actor=None,
    expected_at=None,
    note: str = "",
    idempotency_key: str = "",
) -> PurchaseOrder:
    """
    Write a draft order and its lines, or write none of it.

    `lines`: [{"product": <Product>, "quantity": Decimal, "unit_cost": Decimal|None}]

    `unit_cost` falls back to the product's current `cost_price`, which is the
    shop's own last known figure. It is a starting point a buyer corrects, not
    a price the supplier has quoted — and once the order is raised the figure
    is a copy, so a later catalogue edit cannot move it.
    """
    organization_id = branch.organization_id

    if supplier.organization_id != organization_id:
        # Worded as "no such supplier", not "not yours". identity/scoping.py
        # has the argument: the two must read identically.
        raise ProcurementError({"supplier": "No such supplier."})

    if not supplier.is_active:
        raise ProcurementError(
            {"supplier": "That supplier is archived. Reactivate it to order from it."}
        )

    if not lines:
        raise ProcurementError("An order needs at least one product.")

    if idempotency_key:
        existing = (
            PurchaseOrder.objects.filter(
                organization_id=organization_id, idempotency_key=idempotency_key
            )
            .prefetch_related("items")
            .first()
        )
        if existing is not None:
            return existing

    seen: set[int] = set()
    prepared = []
    for line in lines:
        product = line["product"]
        if product.organization_id != organization_id:
            raise ProcurementError({"product": "No such product."})
        if product.pk in seen:
            raise ProcurementError(
                {
                    "product": (
                        f"{product.name} is on this order twice. "
                        "Put the whole quantity on one line."
                    )
                }
            )
        seen.add(product.pk)

        quantity = as_quantity(line.get("quantity"))
        cost = line.get("unit_cost")
        unit_cost = money(cost if cost is not None else product.cost_price)
        if unit_cost < ZERO:
            raise ProcurementError({"unit_cost": "A cost cannot be negative."})

        prepared.append((product, quantity, unit_cost, money(quantity * unit_cost)))

    _hold_the_sequence(organization_id)
    order = PurchaseOrder.objects.create(
        organization_id=organization_id,
        branch=branch,
        supplier=supplier,
        number=_next_number(PurchaseOrder, organization_id, FIRST_ORDER_NUMBER),
        status=PurchaseOrder.Status.DRAFT,
        expected_at=expected_at,
        note=note,
        total=money(sum((line[3] for line in prepared), ZERO)),
        idempotency_key=idempotency_key,
        **_actor("raised_by", actor),
    )

    PurchaseOrderItem.objects.bulk_create(
        [
            PurchaseOrderItem(
                purchase_order=order,
                product=product,
                product_name=product.name,
                product_sku=product.sku,
                quantity_ordered=quantity,
                unit_cost=unit_cost,
                line_total=line_total,
            )
            for product, quantity, unit_cost, line_total in prepared
        ]
    )

    return order


@transaction.atomic
def replace_lines(order: PurchaseOrder, *, lines: list[dict]) -> PurchaseOrder:
    """
    Rewrite a DRAFT order's lines.

    ⚠ DRAFT ONLY, and the check is here rather than in the serialiser. Editing
      an order a supplier has already been sent means the paperwork in their
      hand and the paperwork in ours disagree, and the delivery that arrives
      matches neither.
    """
    if not order.is_editable:
        raise ProcurementError(
            {
                "status": (
                    "That order has been sent. Cancel it and raise another, "
                    "or receive what arrives."
                )
            }
        )

    if not lines:
        raise ProcurementError("An order needs at least one product.")

    locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    locked.items.all().delete()

    prepared = []
    seen: set[int] = set()
    for line in lines:
        product = line["product"]
        if product.organization_id != locked.organization_id:
            raise ProcurementError({"product": "No such product."})
        if product.pk in seen:
            raise ProcurementError(
                {"product": f"{product.name} is on this order twice."}
            )
        seen.add(product.pk)

        quantity = as_quantity(line.get("quantity"))
        cost = line.get("unit_cost")
        unit_cost = money(cost if cost is not None else product.cost_price)
        prepared.append((product, quantity, unit_cost, money(quantity * unit_cost)))

    PurchaseOrderItem.objects.bulk_create(
        [
            PurchaseOrderItem(
                purchase_order=locked,
                product=product,
                product_name=product.name,
                product_sku=product.sku,
                quantity_ordered=quantity,
                unit_cost=unit_cost,
                line_total=line_total,
            )
            for product, quantity, unit_cost, line_total in prepared
        ]
    )

    locked.total = money(sum((line[3] for line in prepared), ZERO))
    locked.save(update_fields=["total", "updated_at"])
    return locked


# ── the one-way street ───────────────────────────────────────────────────────


@transaction.atomic
def submit_order(order: PurchaseOrder) -> PurchaseOrder:
    """Draft → sent to the supplier. After this the lines are fixed."""
    locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if locked.status != PurchaseOrder.Status.DRAFT:
        raise ProcurementError({"status": "Only a draft can be sent."})
    if not locked.items.exists():
        raise ProcurementError("An order needs at least one product.")

    locked.status = PurchaseOrder.Status.SUBMITTED
    locked.save(update_fields=["status", "updated_at"])

    # The notification the access model implies. `purchasing.manage` and
    # `purchasing.approve` are held by different people on purpose, and until
    # now the approver had no way to learn an order was waiting — so a control
    # that separates two people depended on one of them remembering to look.
    notifications.order_awaiting_approval(order=locked)

    return locked


@transaction.atomic
def approve_order(order: PurchaseOrder, *, actor) -> PurchaseOrder:
    """
    Commit the shop to the money.

    ⚠ AN APPROVAL WITH NO APPROVER IS NOT AN APPROVAL. Unlike `raise_order`,
      which tolerates an unattributable actor, this refuses one: the row
      exists to answer "who agreed to spend this", and a NULL in both columns
      answers nobody.
    """
    attribution = _actor("approved_by", actor)
    if not attribution:
        raise ProcurementError(
            {"detail": "An approval has to be recorded against a person."}
        )

    locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if locked.status != PurchaseOrder.Status.SUBMITTED:
        if locked.status == PurchaseOrder.Status.DRAFT:
            raise ProcurementError(
                {"status": "Send the order to the supplier before approving it."}
            )
        raise ProcurementError({"status": "That order has already been decided."})

    for field, value in attribution.items():
        setattr(locked, field, value)
    locked.status = PurchaseOrder.Status.APPROVED
    locked.approved_at = timezone.now()
    locked.save(
        update_fields=[
            "status",
            "approved_at",
            "approved_by_staff",
            "approved_by_account",
            "updated_at",
        ]
    )

    # It is no longer waiting, so the notification stops being true and leaves
    # the feed. A list of approvals that keeps approved orders in it is a list
    # nobody trusts after the first week.
    notifications.order_approved(order=locked)
    return locked


@transaction.atomic
def cancel_order(order: PurchaseOrder, *, reason: str = "") -> PurchaseOrder:
    """
    Stop an order.

    Anything already received stays received — the stock is on the shelf and
    the movements that put it there are not reversible by changing a status.
    Cancelling a part-received order means "nothing further is coming", which
    is a real thing a supplier says and a state the shop has to be able to
    record.
    """
    locked = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if locked.status not in PurchaseOrder.OPEN:
        raise ProcurementError({"status": "That order is already closed."})

    locked.status = PurchaseOrder.Status.CANCELLED
    locked.cancelled_at = timezone.now()
    locked.cancelled_reason = reason
    locked.save(
        update_fields=["status", "cancelled_at", "cancelled_reason", "updated_at"]
    )

    # Cancelling is the OTHER way an order stops waiting, and it is the one
    # that would have been forgotten: approval is the happy path people think
    # about, so a cancelled order would have sat in the approver's list for
    # ever asking to be approved. Same resolution, because "no longer waiting"
    # is the same fact however it was reached.
    notifications.order_approved(order=locked)

    return locked


# ── receiving ────────────────────────────────────────────────────────────────


def _book_in(branch, product, quantity: Decimal, reference: str) -> None:
    """
    Raise one product's stock and write the movement that explains it.

    The inventory row is created if the branch has never stocked this product
    — a first delivery of a new line is the ordinary case, and refusing it
    because nobody had pre-created an empty shelf row would send a buyer to
    another screen mid-delivery. `stock_product` is idempotent and starts at
    zero, so the quantity still arrives through a movement.
    """
    inventory = stock_product(product=product, branch=branch)

    locked = BranchInventory.objects.select_for_update().get(pk=inventory.pk)
    before = locked.quantity
    locked.quantity = before + quantity
    locked.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        inventory=locked,
        movement_type="PURCHASE",
        quantity_before=before,
        quantity_after=locked.quantity,
        reference=reference,
    )

    # The other direction: a delivery is what makes a low-stock notification
    # stop being true, so this is where it gets resolved.
    notifications.stock_level_changed(
        inventory=locked, before=before, after=locked.quantity
    )


@transaction.atomic
def receive_goods(
    order: PurchaseOrder,
    *,
    lines: list[dict],
    actor=None,
    delivery_note: str = "",
    note: str = "",
    idempotency_key: str = "",
) -> GoodsReceipt:
    """
    Book a delivery against an order, or book none of it.

    `lines`: [{"item": <PurchaseOrderItem>, "quantity": Decimal}]

    Returns the GoodsReceipt. Raises ProcurementError with something the
    person at the door can act on, and writes nothing when it does.
    """
    organization_id = order.organization_id

    if idempotency_key:
        existing = (
            GoodsReceipt.objects.filter(
                organization_id=organization_id, idempotency_key=idempotency_key
            )
            .prefetch_related("items")
            .first()
        )
        if existing is not None:
            return existing

    locked_order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)

    if locked_order.status not in PurchaseOrder.RECEIVABLE:
        if locked_order.status in (
            PurchaseOrder.Status.DRAFT,
            PurchaseOrder.Status.SUBMITTED,
        ):
            raise ProcurementError(
                {
                    "status": (
                        "That order has not been approved yet. Stock cannot be "
                        "booked in against it."
                    )
                }
            )
        raise ProcurementError({"status": "That order is closed."})

    if not lines:
        raise ProcurementError("Say what arrived.")

    # ── THE ORDER OF THE LOCKS IS THE POINT ─────────────────────────────────
    # Two deliveries at two branches of the same shop can touch the same
    # products. Each taking its stock rows in product-id order means one waits
    # instead of both deadlocking — the same reasoning, and the same sort, as
    # `sales.services._lock_inventory`.
    # ── THE LINES ARE RE-READ UNDER THE LOCK ────────────────────────────────
    #
    # They arrived resolved by a serialiser, which read them BEFORE the order
    # was locked. Two deliveries booked at once would each have loaded
    # `quantity_received = 0`, and the second would measure its own quantity
    # against an outstanding figure the first has already consumed — the
    # over-receipt check would pass twice and the shop would book twenty
    # against an order for ten.
    fresh = {
        item.pk: item
        # `select_related` because every line now reads its product's pack
        # size, which would otherwise be a query per line in the middle of
        # a locked transaction. `of=("self",)` keeps the lock on the order
        # lines alone — locking catalog rows here would make unloading a
        # lorry block somebody editing a price in the office.
        for item in PurchaseOrderItem.objects.select_for_update(of=("self",))
        .select_related("product")
        .filter(purchase_order=locked_order)
    }

    prepared = []
    seen: set[int] = set()
    for line in lines:
        item = fresh.get(line["item"].pk)
        if item is None:
            raise ProcurementError({"item": "That line is not on this order."})
        if item.pk in seen:
            raise ProcurementError(
                {
                    "item": (
                        f"{item.product_name} is on this delivery twice. "
                        "Count it once."
                    )
                }
            )
        seen.add(item.pk)

        quantity = as_quantity(line.get("quantity"))
        if quantity > item.outstanding:
            raise ProcurementError(
                {
                    "quantity": (
                        f"Only {item.outstanding} of {item.product_name} are "
                        f"still outstanding on this order. Raise another order "
                        f"for the extra, so what arrived is still explained by "
                        f"what was asked for."
                    )
                }
            )
        prepared.append((item, quantity))

    prepared.sort(key=lambda pair: pair[0].product_id)

    _hold_the_sequence(organization_id)
    receipt = GoodsReceipt.objects.create(
        organization_id=organization_id,
        branch=locked_order.branch,
        purchase_order=locked_order,
        number=_next_number(GoodsReceipt, organization_id, FIRST_RECEIPT_NUMBER),
        delivery_note=delivery_note,
        note=note,
        idempotency_key=idempotency_key,
        **_actor("received_by", actor),
    )

    reference = f"Delivery #{receipt.number} · Order #{locked_order.number}"

    for item, quantity in prepared:
        # ── PACKS IN, UNITS ONTO THE SHELF ──────────────────────────────
        #
        # The order and the delivery note are both in packs, because that
        # is what was bought and what was counted off the lorry. The shelf
        # is in sellable units, because that is what a cashier rings up.
        #
        # They were the same number, so ten cartons of soda put TEN on the
        # shelf and the till ran out after ten bottles with the storeroom
        # full. `units_in` is the only place the conversion happens.
        #
        # The pack size is copied onto the line as it stands now — see the
        # banner on GoodsReceiptItem.units_per_pack for why a delivery must
        # not change meaning when a product is edited next year.
        per_pack = item.product.units_per_pack
        GoodsReceiptItem.objects.create(
            receipt=receipt,
            order_item=item,
            quantity=quantity,
            unit_cost=item.unit_cost,
            units_per_pack=per_pack,
        )
        _book_in(
            locked_order.branch,
            item.product,
            item.product.units_in(quantity),
            reference,
        )

        item.quantity_received = item.quantity_received + quantity
        item.save(update_fields=["quantity_received"])

    _restate_status(locked_order)
    return receipt


def _restate_status(order: PurchaseOrder) -> None:
    """
    Where the order stands now that something has arrived.

    Computed from the lines rather than incremented, because the lines are the
    record and a counter that drifts from them is a closed order with stock
    still owed.
    """
    items = list(order.items.all())
    if items and all(item.outstanding <= ZERO for item in items):
        order.status = PurchaseOrder.Status.RECEIVED
    else:
        order.status = PurchaseOrder.Status.PART_RECEIVED
    order.save(update_fields=["status", "updated_at"])
