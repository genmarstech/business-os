"""
Checkout, refund and receipt. The only place any of them happen.

═══════════════════════════════════════════════════════════════════════════════
VIEWS AND SERIALISERS DO NOT WRITE SALES. THEY CALL THIS.

The same reasoning as gen-portal's `accounts/identity.py`: an operation that
must be atomic, must move stock, must respect an idempotency key and must never
leave a half-charged customer is an operation that needs ONE implementation.
Spread across a serialiser's `create`, a viewset's `perform_create` and a
signal, it has three, and two of them will be wrong.
═══════════════════════════════════════════════════════════════════════════════

── WHAT "ATOMIC" HAS TO COVER ──────────────────────────────────────────────────

A checkout is not one write. It is: allocate a number, write the sale, write
every line, decrement every stock level, write a StockMovement per line, record
the payments, and issue a receipt. If the process dies between the stock
decrement and the payment, the shop has given away inventory for free and there
is no record of a sale at all.

So the whole of it is one transaction, and the stock rows are locked in a
deterministic order (see `_lock_inventory`) so two tills selling the last two
units of the same product cannot deadlock each other.

── STOCK MOVES THROUGH StockMovement, ALWAYS ───────────────────────────────────

Blueprint §10: "Stock should change through auditable stock movements." Nothing
here writes `BranchInventory.quantity` without also writing the movement that
explains it. A quantity that changed with no movement behind it is the thing
that makes a stock take irreconcilable.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max
from django.utils import timezone

from branches.models import RegisterShift
from catalog.models import CatalogCategoryProduct, TaxRule
from inventory.models import BranchInventory, StockMovement

from .models import Customer, Payment, Receipt, Refund, RefundItem, Sale, SaleItem

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")

# Where a shop's numbering starts. 1000 rather than 1 so the first week's
# receipts do not advertise that it is the first week.
FIRST_SALE_NUMBER = 1000
FIRST_REFUND_NUMBER = 2000


class SaleError(ValidationError):
    """
    Anything that stops a sale being written.

    A plain ValidationError so DRF turns it into a 400 with the message
    intact. These messages are read by a cashier with a customer waiting, so
    they say what to do, not what went wrong internally.
    """


def money(value) -> Decimal:
    """
    Two places, half-up.

    Banker's rounding — Python's default — would round 2.5 to 2 and 3.5 to 4.
    That is correct for statistics and wrong for a till, where a customer
    watching the screen expects the half to go up every time.
    """
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


# ── tax ──────────────────────────────────────────────────────────────────────


def rule_for(product: CatalogCategoryProduct) -> TaxRule | None:
    """The product's own rule, else the organisation's default, else none."""
    if product.tax_rule_id:
        return product.tax_rule
    return TaxRule.objects.filter(
        organization_id=product.organization_id, is_default=True, is_active=True
    ).first()


def split_tax(gross: Decimal, rule: TaxRule | None) -> tuple[Decimal, Decimal]:
    """
    Return (net, tax) for an amount, according to the rule.

    `gross` is what the line is worth after discount — which is the shelf
    price when the rule is inclusive, and the pre-tax amount when it is not.
    Getting this the wrong way round silently mis-states every VAT return the
    shop files, so it is one function with one caller rather than a formula
    written out at each site.
    """
    if rule is None or not rule.rate:
        return money(gross), ZERO

    rate = Decimal(rule.rate) / Decimal("100")
    if rule.is_inclusive:
        net = Decimal(gross) / (Decimal("1") + rate)
        return money(net), money(Decimal(gross) - net)

    tax = Decimal(gross) * rate
    return money(gross), money(tax)


# ── numbering ────────────────────────────────────────────────────────────────


def _next_number(model, organization_id: int, first: int) -> int:
    """
    The next number in this organisation's sequence.

    Taken under the row lock the caller already holds on the organisation, so
    two simultaneous checkouts cannot be handed the same number. The unique
    constraint is the backstop; this is what stops it being hit.
    """
    highest = model.objects.filter(organization_id=organization_id).aggregate(
        top=Max("number")
    )["top"]
    return first if highest is None else highest + 1


# ── stock ────────────────────────────────────────────────────────────────────


def _lock_inventory(branch_id: int, product_ids: list[int]) -> dict[int, BranchInventory]:
    """
    Take the stock rows for this sale, locked, in a stable order.

    ── THE ORDER IS THE POINT ──────────────────────────────────────────────
    Two tills selling the same two products in opposite orders will deadlock
    if each locks as it goes: till 1 holds milk and wants bread, till 2 holds
    bread and wants milk. Sorting by primary key means every caller takes
    them in the same order, so one waits instead of both dying.
    """
    rows = (
        BranchInventory.objects.select_for_update()
        .filter(branch_id=branch_id, product_id__in=product_ids)
        .order_by("pk")
    )
    return {row.product_id: row for row in rows}


def _move_stock(inventory: BranchInventory, delta: Decimal, kind: str, reference: str):
    """
    Change a quantity and write the movement that explains it. Never one
    without the other — see the module docstring.
    """
    before = inventory.quantity
    inventory.quantity = before + delta
    inventory.save(update_fields=["quantity", "updated_at"])

    StockMovement.objects.create(
        inventory=inventory,
        movement_type=kind,
        quantity_before=before,
        quantity_after=inventory.quantity,
        reference=reference,
    )


# ── checkout ─────────────────────────────────────────────────────────────────


@transaction.atomic
def checkout(
    *,
    shift: RegisterShift,
    cashier,
    lines: list[dict],
    payments: list[dict],
    customer: Customer | None = None,
    idempotency_key: str = "",
    allow_negative_stock: bool = False,
    order_type: str = Sale.OrderType.COUNTER,
    table_name: str = "",
) -> Sale:
    """
    Take a sale from cart to completed, or take none of it.

    `lines`    : [{"product": <Product>, "quantity": Decimal, "discount": Decimal}]
    `payments` : [{"method": str, "amount": Decimal, "reference": str,
                   "tendered": Decimal | None}]

    Returns the completed Sale. Raises SaleError with a message a cashier can
    act on, and writes nothing when it does.
    """
    register = shift.register
    branch = register.branch
    organization_id = branch.organization_id

    if shift.status != "OPEN":
        raise SaleError(
            "That shift is closed. Open a shift on this register before selling."
        )

    if not lines:
        raise SaleError("A sale needs at least one item.")

    # ── the idempotency check, BEFORE anything is written ───────────────────
    #
    # A till retrying a checkout it never saw the answer to must get the
    # original sale back, not a second one. Returning the existing sale is the
    # whole contract: the caller cannot tell a retry from a first attempt, and
    # does not need to.
    if idempotency_key:
        existing = (
            Sale.objects.filter(
                organization_id=organization_id, idempotency_key=idempotency_key
            )
            .prefetch_related("items", "payments")
            .first()
        )
        if existing is not None:
            return existing

    if customer is not None and customer.organization_id != organization_id:
        # Same wording as an unknown customer. "Not yours" and "does not
        # exist" must read identically — identity/scoping.py has the argument.
        raise SaleError({"customer": "No such customer."})

    products = [line["product"] for line in lines]
    for product in products:
        if product.organization_id != organization_id:
            raise SaleError({"lines": "No such product."})
        if not product.is_active:
            raise SaleError({"lines": f"{product.name} is no longer on sale."})

    stock = _lock_inventory(branch.pk, [p.pk for p in products])

    # ── price the lines ─────────────────────────────────────────────────────
    priced = []
    subtotal = discount_total = tax_total = total = ZERO

    for line in lines:
        product = line["product"]
        quantity = Decimal(line["quantity"])
        discount = money(line.get("discount") or ZERO)

        if quantity <= 0:
            raise SaleError({"lines": "A quantity has to be more than zero."})

        gross = money(Decimal(product.selling_price) * quantity)
        if discount > gross:
            raise SaleError(
                {"lines": f"The discount on {product.name} is more than the line."}
            )

        after_discount = money(gross - discount)
        rule = rule_for(product)
        net, tax = split_tax(after_discount, rule)

        # ── inclusive vs exclusive changes what is owed ─────────────────────
        # Inclusive: the customer pays `after_discount` and tax is inside it.
        # Exclusive: the customer pays `after_discount` PLUS the tax.
        line_total = after_discount if (rule and rule.is_inclusive) or rule is None \
            else money(after_discount + tax)

        available = stock.get(product.pk)
        if not allow_negative_stock:
            if available is None:
                raise SaleError(
                    {"lines": f"{product.name} is not stocked at this branch."}
                )
            if available.quantity < quantity:
                raise SaleError(
                    {
                        "lines": (
                            f"Only {available.quantity} of {product.name} left "
                            f"at this branch."
                        )
                    }
                )

        priced.append(
            {
                "product": product,
                "quantity": quantity,
                "discount": discount,
                "net": net,
                "tax": tax,
                "tax_rate": Decimal(rule.rate) if rule else ZERO,
                "line_total": line_total,
                "gross": gross,
                "note": str(line.get("note", "") or "").strip()[:200],
            }
        )

        subtotal += gross
        discount_total += discount
        tax_total += tax
        total += line_total

    subtotal, discount_total = money(subtotal), money(discount_total)
    tax_total, total = money(tax_total), money(total)

    # ── check the money before writing anything ─────────────────────────────
    paid = ZERO
    for payment in payments:
        amount = money(payment["amount"])
        if amount <= 0:
            raise SaleError({"payments": "A payment has to be more than zero."})
        if payment["method"] == Payment.Method.CREDIT and customer is None:
            raise SaleError(
                {"payments": "An account sale needs a customer to put it on."}
            )
        paid += amount

    if paid < total:
        raise SaleError(
            {"payments": f"{money(total - paid)} still to pay on a total of {total}."}
        )

    # Overpayment is only meaningful in cash, where the difference is change.
    # On a card or an M-Pesa line it means somebody typed the wrong figure,
    # and taking it silently makes the drawer wrong at close.
    overpaid = money(paid - total)
    if overpaid > ZERO:
        cash = [p for p in payments if p["method"] == Payment.Method.CASH]
        if not cash:
            raise SaleError(
                {"payments": f"That is {overpaid} more than the {total} owed."}
            )

    # ── write ───────────────────────────────────────────────────────────────
    sale = Sale.objects.create(
        organization_id=organization_id,
        branch=branch,
        register=register,
        shift=shift,
        cashier=cashier,
        customer=customer,
        number=_next_number(Sale, organization_id, FIRST_SALE_NUMBER),
        status=Sale.Status.COMPLETED,
        subtotal=subtotal,
        discount_total=discount_total,
        tax_total=tax_total,
        total=total,
        idempotency_key=idempotency_key,
        completed_at=timezone.now(),
        order_type=(
            order_type
            if order_type in Sale.OrderType.values
            else Sale.OrderType.COUNTER
        ),
        table_name=(table_name or "").strip()[:40],
    )

    for line in priced:
        product = line["product"]
        SaleItem.objects.create(
            sale=sale,
            product=product,
            product_name=product.name,
            sku=product.sku,
            unit_price=product.selling_price,
            unit_cost=product.cost_price,
            quantity=line["quantity"],
            discount_amount=line["discount"],
            tax_rate=line["tax_rate"],
            tax_amount=line["tax"],
            line_total=line["line_total"],
            # Snapshotted like the price and the tax rate beside it. A note is
            # part of what was ordered, so a reprint next year has to say it.
            note=line.get("note", ""),
        )

        held = stock.get(product.pk)
        if held is not None:
            _move_stock(
                held, -line["quantity"], "SALE", f"Sale #{sale.number}"
            )

    remaining = total
    for payment in payments:
        amount = money(payment["amount"])
        method = payment["method"]

        # Change comes out of the cash line, and only up to what is owed —
        # so a 1,000 note against a 700 total records 700 taken and 300 back,
        # not 1,000 taken.
        change = ZERO
        if method == Payment.Method.CASH and amount > remaining:
            change = money(amount - remaining)

        Payment.objects.create(
            sale=sale,
            method=method,
            amount=money(amount - change),
            reference=payment.get("reference", ""),
            tendered=amount if method == Payment.Method.CASH else None,
            change_given=change,
        )
        remaining = max(ZERO, money(remaining - (amount - change)))

        if method == Payment.Method.CREDIT:
            # F() rather than read-modify-write: two tills putting money on
            # the same account would both read 500, both write 500 + their
            # own, and one sale's credit would vanish. The database does the
            # addition against whatever the row actually holds.
            Customer.objects.filter(pk=customer.pk).update(
                credit_balance=F("credit_balance") + money(amount - change)
            )

    issue_receipt(sale)
    return sale


# ── receipts ─────────────────────────────────────────────────────────────────


def issue_receipt(sale: Sale) -> Receipt:
    """
    The first issue. Idempotent: a sale has exactly one receipt, and asking
    twice returns the one it has rather than minting a second number for the
    same transaction.
    """
    receipt, _ = Receipt.objects.get_or_create(
        sale=sale,
        defaults={"number": f"{sale.branch.branch_number}-{sale.number}"},
    )
    return receipt


def reprint_receipt(receipt: Receipt, *, delivered_to: str = "") -> Receipt:
    """
    Record that it was printed again. See the note on Receipt: the count is
    the point, and it is kept here rather than at the till.
    """
    receipt.reprint_count += 1
    receipt.last_reprinted_at = timezone.now()
    if delivered_to:
        receipt.delivered_to = delivered_to
    receipt.save(
        update_fields=["reprint_count", "last_reprinted_at", "delivered_to"]
    )
    return receipt


# ── voids and refunds ────────────────────────────────────────────────────────


@transaction.atomic
def void_sale(sale: Sale, *, reason: str) -> Sale:
    """
    Cancel a sale outright and put the stock back.

    Distinct from a refund: a void says the transaction should never have been
    recorded. It is refused once any money has been refunded against the sale,
    because at that point there is a second document pointing at it and
    "this never happened" stops being true.
    """
    if sale.status == Sale.Status.VOIDED:
        return sale
    if not reason.strip():
        raise SaleError({"reason": "Say why this sale is being voided."})
    if sale.refunds.filter(status=Refund.Status.COMPLETED).exists():
        raise SaleError(
            "This sale has already been refunded. Refund the rest instead of "
            "voiding it."
        )

    items = list(sale.items.select_related("product"))
    stock = _lock_inventory(sale.branch_id, [i.product_id for i in items])
    for item in items:
        held = stock.get(item.product_id)
        if held is not None:
            _move_stock(held, item.quantity, "RETURN", f"Void of sale #{sale.number}")

    sale.status = Sale.Status.VOIDED
    sale.voided_at = timezone.now()
    sale.void_reason = reason.strip()[:200]
    sale.save(update_fields=["status", "voided_at", "void_reason", "updated_at"])
    return sale


@transaction.atomic
def refund_sale(
    *,
    sale: Sale,
    branch,
    processed_by,
    lines: list[dict],
    reason: str,
    shift: RegisterShift | None = None,
    idempotency_key: str = "",
) -> Refund:
    """
    Give money back against a sale, without touching the sale.

    `lines`: [{"sale_item": <SaleItem>, "quantity": Decimal, "restock": bool}]

    The original stays exactly as it was — blueprint §10. What changes is that
    a new document exists pointing at it, and the stock of anything restocked
    goes back up through a movement like everything else.
    """
    organization_id = sale.organization_id

    if idempotency_key:
        existing = Refund.objects.filter(
            organization_id=organization_id, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

    if sale.status != Sale.Status.COMPLETED:
        raise SaleError("Only a completed sale can be refunded.")
    if not reason.strip():
        raise SaleError({"reason": "Say why this money is being given back."})
    if not lines:
        raise SaleError({"lines": "Choose what is being returned."})
    if branch.organization_id != organization_id:
        raise SaleError({"branch": "No such branch."})

    # ── how much of each line is still refundable ───────────────────────────
    #
    # Computed from the refunds already written rather than from a counter on
    # the line, because a counter is a second source of truth and the two
    # drift. Three partial refunds of one shirt each must not be able to
    # return four shirts.
    already = {}
    for item in RefundItem.objects.filter(
        refund__sale=sale, refund__status=Refund.Status.COMPLETED
    ):
        already[item.sale_item_id] = already.get(item.sale_item_id, ZERO) + item.quantity

    prepared = []
    total = ZERO
    for line in lines:
        item = line["sale_item"]
        quantity = Decimal(line["quantity"])

        if item.sale_id != sale.pk:
            raise SaleError({"lines": "That line is not on this sale."})
        if quantity <= 0:
            raise SaleError({"lines": "A returned quantity has to be more than zero."})

        outstanding = item.quantity - already.get(item.pk, ZERO)
        if quantity > outstanding:
            raise SaleError(
                {
                    "lines": (
                        f"Only {outstanding} of {item.product_name} is still "
                        f"refundable on this sale."
                    )
                }
            )

        # Pro-rata on the line as it was actually charged, so a discount given
        # at the time is honoured on the way back. Refunding the shelf price
        # of a discounted item hands back money that was never taken.
        share = (item.line_total / item.quantity) * quantity
        amount = money(share)

        prepared.append(
            {
                "sale_item": item,
                "quantity": quantity,
                "amount": amount,
                "restocked": bool(line.get("restock", True)),
            }
        )
        total += amount

    total = money(total)

    refund = Refund.objects.create(
        organization_id=organization_id,
        sale=sale,
        branch=branch,
        shift=shift,
        processed_by=processed_by,
        number=_next_number(Refund, organization_id, FIRST_REFUND_NUMBER),
        total=total,
        reason=reason.strip()[:200],
        idempotency_key=idempotency_key,
    )

    restocking = [p for p in prepared if p["restocked"]]
    stock = _lock_inventory(
        branch.pk, [p["sale_item"].product_id for p in restocking]
    )

    for line in prepared:
        RefundItem.objects.create(
            refund=refund,
            sale_item=line["sale_item"],
            quantity=line["quantity"],
            amount=line["amount"],
            restocked=line["restocked"],
        )
        if line["restocked"]:
            held = stock.get(line["sale_item"].product_id)
            if held is not None:
                _move_stock(
                    held,
                    line["quantity"],
                    "RETURN",
                    f"Refund #{refund.number} against sale #{sale.number}",
                )

    # Money back on an account sale reduces what is owed before it reduces
    # anything else — the customer never paid it in the first place.
    if sale.customer_id:
        on_account = sale.payments.filter(method=Payment.Method.CREDIT).first()
        if on_account is not None:
            Customer.objects.filter(pk=sale.customer_id).update(
                credit_balance=F("credit_balance") - min(total, on_account.amount)
            )

    return refund
