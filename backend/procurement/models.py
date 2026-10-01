"""
Procurement: what was ordered from whom, and what actually turned up.

═══════════════════════════════════════════════════════════════════════════════
BLUEPRINT §9 — the last domain in the table that said "not built".

    "Procurement: Supplier, PurchaseOrder, GoodsReceipt."

§12 puts it at the top of V2, and it is the other half of a stock figure. Until
now the only way stock went UP was a manual adjustment: somebody typed a number
and the shop took their word for it. A delivery is not a number somebody typed
— it is a document from a supplier, against an order the shop placed, counted
at the door by a person who is not the person who ordered it.
═══════════════════════════════════════════════════════════════════════════════

── THE SAME THREE RULES THE SALES SIDE IS BUILT ON ─────────────────────────────

Deliberately, because a shop's two money flows should not disagree about what a
document is:

  · **Every cost on an order line is a copy.** A supplier raising their price
    next month must not rewrite what this order committed to, any more than
    raising a shelf price rewrites a receipt from last week. `product_name`,
    `product_sku` and `unit_cost` are stamped onto the line when it is raised.
  · **Nothing edits a document that has left the building.** A purchase order
    may be changed while it is a DRAFT. Once it is submitted it is a thing a
    supplier has been told; the ways out are approval, receipt and
    cancellation, each of which leaves the original readable.
  · **Numbering is per organisation.** Order #3000 is this shop's first order.
    A platform-wide sequence would let every tenant measure every other
    tenant's buying — the same oracle `Sale.number` avoids.

── WHAT IS DELIBERATELY NOT HERE ───────────────────────────────────────────────

**Purchase tax.** A supplier invoice carries VAT, and what a shop may reclaim
of it is an accounting question with a filing attached. §12 puts accounting
integrations in V3, and a half-modelled input-VAT column would be read as an
answer by whoever eventually files the return. The order carries what it will
cost; the invoice stays with the accountant until there is somewhere real to
put it.

**Receiving does not rewrite `CatalogCategoryProduct.cost_price`.** It is the
obvious next line of code and it is wrong by default: cost_price is what every
margin report in the system measures against, so a single delivery at a
promotional price would silently restate the profitability of everything sold
before it. Moving a cost is a decision somebody makes, not a side effect of a
lorry arriving.

── WHO DID IT: TWO COLUMNS, BECAUSE THERE ARE TWO KINDS OF PERSON ──────────────

An order can be raised by a purchasing officer (an `OrganizationStaff` row,
signed in at a till session) or by the owner (a `PlatformAccount`, signed in
with their Genmars identity). They are different tables on purpose — see the
two-tier identity note in identity/models.py — so attribution needs one
nullable key for each, and a constraint saying at most one is set.

The alternative was a single free-text "raised by" column, which is not
attribution: an approval that cannot be traced to a row that still exists is a
name somebody typed.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from branches.models import Branches
from catalog.models import CatalogCategoryProduct
from identity.models import PlatformAccount
from organisations.models import BusinessOrganization, OrganizationStaff

# Same shape as sales.models.MONEY, and not imported from it: procurement does
# not depend on sales and should not start to. Two decimal places on every
# money column, and a ceiling that clears a container load without inviting a
# 15-digit typo.
MONEY = {"max_digits": 12, "decimal_places": 2}

ZERO = Decimal("0.00")
QUANTITY = {"max_digits": 12, "decimal_places": 2}


class Supplier(models.Model):
    """
    Somebody the shop buys from.

    ── NOTHING HERE IS GLOBALLY UNIQUE ─────────────────────────────────────
    Two shops both buy from Kenya Breweries, and they are two rows. A shared
    supplier table would mean one tenant's edit changing another tenant's
    contact details, and a uniqueness error telling whoever tripped it that a
    supplier exists in a shop they cannot see — the oracle that
    OrganizationStaff and CatalogCategories both have written out at length.
    """

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="suppliers"
    )

    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=120, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)

    # ⚠ A supplier's KRA PIN is another business's tax identifier, which makes
    #   it the customer's data to hold and ours to process — the same note
    #   OrganizationStaff carries about its staff. Optional, and it stays
    #   optional until something in the system genuinely needs it.
    kra_pin = models.CharField(max_length=20, blank=True)

    # How long this supplier usually takes, in days. Used to suggest a
    # delivery date when an order is raised; it is a hint and nothing reads it
    # as a promise.
    lead_time_days = models.PositiveSmallIntegerField(default=0)

    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_supplier_name_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "phone_number"],
                condition=~models.Q(phone_number=""),
                name="unique_supplier_phone_per_organization",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class PurchaseOrder(models.Model):
    """
    What the shop asked a supplier for, and where it is in its life.

    ── THE STATUSES ARE A ONE-WAY STREET ───────────────────────────────────

        DRAFT ──submit──▶ SUBMITTED ──approve──▶ APPROVED
                                                    │
                                        receive ──▶ PART_RECEIVED ──▶ RECEIVED
          └────────────────── cancel ──────────────────┘

    RECEIVED and CANCELLED are terminal. Nothing goes backwards: an order that
    was approved and then "un-approved" leaves no trace of the approval, and
    the approval is the control this whole model exists to record.

    ── IT IS RAISED BY ONE PERSON AND APPROVED BY ANOTHER ──────────────────
    Not by an identity check in a view, but by the permission map: a
    purchasing officer holds `purchasing.manage` and not `purchasing.approve`,
    a branch manager holds the reverse. Same construction as a cashier being
    unable to count their own till — the cheapest second person is a
    permission the first one does not hold.

    An owner holds both, and in a shop whose entire back office is one person
    that is the only workable answer. The control is real wherever there are
    two people and advisory where there is one; pretending otherwise would
    just stop the smallest customers from buying stock.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Sent to the supplier"
        APPROVED = "approved", "Approved"
        PART_RECEIVED = "part_received", "Partly received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses a delivery may be booked against. Named here rather than
    # written out at each call site, because the day a status is added is the
    # day one of those call sites is missed.
    RECEIVABLE = frozenset({Status.APPROVED, Status.PART_RECEIVED})
    OPEN = frozenset(
        {Status.DRAFT, Status.SUBMITTED, Status.APPROVED, Status.PART_RECEIVED}
    )

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.PROTECT, related_name="purchase_orders"
    )

    # Stock arrives somewhere. An order with no branch could not be received
    # into anything, and §8 scoping needs one hop to a branch rather than two
    # — the same denormalisation, for the same reason, as `Sale.branch`.
    branch = models.ForeignKey(
        Branches, on_delete=models.PROTECT, related_name="purchase_orders"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="purchase_orders"
    )

    number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )

    expected_at = models.DateField(
        null=True,
        blank=True,
        help_text="When the shop expects it. A hint, not a commitment.",
    )
    note = models.TextField(blank=True)

    # Stored, not computed on read. A total that recalculates itself changes
    # when the rounding rules do, and this one is a figure a supplier has been
    # sent.
    total = models.DecimalField(**MONEY, default=ZERO)

    raised_by_staff = models.ForeignKey(
        OrganizationStaff,
        on_delete=models.PROTECT,
        related_name="purchase_orders_raised",
        null=True,
        blank=True,
    )
    raised_by_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.PROTECT,
        related_name="purchase_orders_raised",
        null=True,
        blank=True,
    )

    approved_by_staff = models.ForeignKey(
        OrganizationStaff,
        on_delete=models.PROTECT,
        related_name="purchase_orders_approved",
        null=True,
        blank=True,
    )
    approved_by_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.PROTECT,
        related_name="purchase_orders_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)

    # §11 asks for idempotency keys to be defined before rollout. An order is
    # not a payment, but a double-tapped "Raise order" on a slow connection is
    # two lorries, and the second one is somebody's money.
    idempotency_key = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="unique_purchase_order_number_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_purchase_order_key_per_organization",
            ),
            # One actor or the other, never both. A row with two "raised by"
            # answers is a row that cannot answer the question.
            models.CheckConstraint(
                condition=(
                    models.Q(raised_by_staff__isnull=True)
                    | models.Q(raised_by_account__isnull=True)
                ),
                name="purchase_order_raised_by_one_actor",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(approved_by_staff__isnull=True)
                    | models.Q(approved_by_account__isnull=True)
                ),
                name="purchase_order_approved_by_one_actor",
            ),
        ]

    def __str__(self) -> str:
        return f"Order #{self.number}"

    @property
    def is_editable(self) -> bool:
        """Only a draft. Everything else has been sent to somebody."""
        return self.status == self.Status.DRAFT

    @property
    def raised_by_name(self) -> str:
        if self.raised_by_staff_id:
            return self.raised_by_staff.full_name
        if self.raised_by_account_id:
            return self.raised_by_account.full_name or self.raised_by_account.email
        return ""

    @property
    def approved_by_name(self) -> str:
        if self.approved_by_staff_id:
            return self.approved_by_staff.full_name
        if self.approved_by_account_id:
            return (
                self.approved_by_account.full_name or self.approved_by_account.email
            )
        return ""


class PurchaseOrderItem(models.Model):
    """
    One product on an order, at the cost it was ordered at.

    `quantity_received` is maintained by `procurement.services` and by nothing
    else. It is the figure that decides whether an order is still open, so a
    serialiser that could write it would let a client close an order nobody
    delivered.
    """

    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(
        CatalogCategoryProduct,
        on_delete=models.PROTECT,
        related_name="purchase_order_items",
    )

    # Copies, taken when the line is written. See the module docstring.
    product_name = models.CharField(max_length=200)
    product_sku = models.CharField(max_length=100)

    quantity_ordered = models.DecimalField(
        **QUANTITY, validators=[MinValueValidator(Decimal("0.01"))]
    )
    quantity_received = models.DecimalField(**QUANTITY, default=ZERO)
    unit_cost = models.DecimalField(**MONEY)
    line_total = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["purchase_order", "product"],
                name="unique_product_per_purchase_order",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product_name} × {self.quantity_ordered}"

    @property
    def outstanding(self) -> Decimal:
        """What is still to come. Never negative — the service refuses over-receipt."""
        return self.quantity_ordered - self.quantity_received


class GoodsReceipt(models.Model):
    """
    A delivery, counted at the door.

    ── IT IS ITS OWN DOCUMENT, NOT A FLAG ON THE ORDER ─────────────────────
    One order can arrive in three lorries over a fortnight, and each of them
    is a separate thing somebody signed for. A `received` boolean would lose
    two of them, and with them the answer to "when did these twelve actually
    turn up" — which is the question asked when a crate is short.

    Same shape as a Refund pointing back at a Sale: the receipt references the
    order, and the order is never rewritten beyond the running total its own
    lines keep.
    """

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.PROTECT, related_name="goods_receipts"
    )
    branch = models.ForeignKey(
        Branches, on_delete=models.PROTECT, related_name="goods_receipts"
    )
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, related_name="receipts"
    )

    number = models.PositiveIntegerField()

    # The supplier's own paperwork reference, as written on the note that came
    # with the goods. Free text because it is their format, not ours.
    delivery_note = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)

    received_by_staff = models.ForeignKey(
        OrganizationStaff,
        on_delete=models.PROTECT,
        related_name="goods_receipts",
        null=True,
        blank=True,
    )
    received_by_account = models.ForeignKey(
        PlatformAccount,
        on_delete=models.PROTECT,
        related_name="goods_receipts",
        null=True,
        blank=True,
    )

    received_at = models.DateTimeField(default=timezone.now)
    idempotency_key = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="unique_goods_receipt_number_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_goods_receipt_key_per_organization",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(received_by_staff__isnull=True)
                    | models.Q(received_by_account__isnull=True)
                ),
                name="goods_receipt_received_by_one_actor",
            ),
        ]

    def __str__(self) -> str:
        return f"Delivery #{self.number}"

    @property
    def received_by_name(self) -> str:
        if self.received_by_staff_id:
            return self.received_by_staff.full_name
        if self.received_by_account_id:
            return (
                self.received_by_account.full_name or self.received_by_account.email
            )
        return ""


class GoodsReceiptItem(models.Model):
    """
    How much of one order line turned up in one delivery.

    `unit_cost` is copied again here, from the order line as it stood. The
    order line already holds it, and a delivery is the document an invoice is
    matched against — if the two ever disagree, the answer has to be readable
    from the delivery itself rather than inferred from whatever the order says
    today.
    """

    receipt = models.ForeignKey(
        GoodsReceipt, on_delete=models.CASCADE, related_name="items"
    )
    order_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.PROTECT, related_name="receipt_lines"
    )

    quantity = models.DecimalField(
        **QUANTITY, validators=[MinValueValidator(Decimal("0.01"))]
    )
    unit_cost = models.DecimalField(**MONEY)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.order_item.product_name} × {self.quantity}"
