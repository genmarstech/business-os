"""
Sales: the checkout, what it took in, and what it gave back.

═══════════════════════════════════════════════════════════════════════════════
BLUEPRINT §10 — TRANSACTION INTEGRITY.

    "Sales and payment records are financial records. Prefer immutable
     transaction history over destructive updates. A refund should reference
     the original sale rather than deleting or rewriting it."

Nothing in this module edits a completed sale. A mistake becomes a VOID with a
reason or a Refund that points back; both leave the original readable, because
the question somebody asks six months later is "what actually happened", and a
table that rewrites itself cannot answer it.
═══════════════════════════════════════════════════════════════════════════════

── EVERY PRICE HERE IS A COPY, NOT A JOIN ──────────────────────────────────────

SaleItem carries the product's name, SKU, unit price and tax rate as they were
at the moment of sale. It does not join to the catalogue to find out what a
line cost.

Raising a price tomorrow must not silently rewrite what a customer paid today,
and renaming a product must not rewrite receipts already printed. gen-portal
makes the same choice on Contract, Invoice and Offer for the same reason.

── MONEY IS Decimal, AND THE TOTALS ARE STORED ─────────────────────────────────

Never float. And the totals are columns rather than properties: a receipt that
recomputes itself on read is a receipt that changes when the rounding rules do.

── NUMBERING IS PER ORGANISATION ───────────────────────────────────────────────

Sale #1001 belongs to one shop's sequence. A platform-wide counter would tell
every tenant how much business every other tenant is doing — watch your own
numbers jump by four hundred overnight and you have measured the competition.
The same reasoning as the scoped uniqueness on OrganizationStaff and
CatalogCategories: a shared sequence is an oracle.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from branches.models import Branches, Register, RegisterShift
from catalog.models import CatalogCategoryProduct
from organisations.models import BusinessOrganization, OrganizationStaff

# Two decimal places on every money column, and a ceiling that comfortably
# clears a day's takings at a busy branch without inviting a 15-digit typo.
MONEY = {"max_digits": 12, "decimal_places": 2}

ZERO = Decimal("0.00")


class Customer(models.Model):
    """
    Somebody who buys, when the shop wants to remember them.

    Optional on a sale, always: a queue does not stop so a walk-in can be
    registered, and a POS that demands a customer before it will take cash is
    a POS nobody uses.

    ── NOTHING HERE IS GLOBALLY UNIQUE ─────────────────────────────────────
    Two shops may each have a customer on 0722 000 000 — quite possibly the
    same human being, who is not one record, because one shop's debtor is not
    another shop's business. Scoped in Meta, for the reason written out at
    length on OrganizationStaff: a uniqueness constraint that spans tenants
    tells whoever trips it that a row exists somewhere they cannot see.
    """

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="customers"
    )

    full_name = models.CharField(max_length=120)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    # ── WHAT THEY OWE, AND WHY IT IS A COLUMN ───────────────────────────────
    #
    # Derived from CREDIT payments and the refunds against them, but stored,
    # because "what does this customer owe" is asked at a till by somebody
    # holding up a queue. Maintained only by sales.services — never set from a
    # serialiser, or the balance becomes whatever the client last sent.
    credit_balance = models.DecimalField(**MONEY, default=ZERO)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "phone_number"],
                condition=~models.Q(phone_number=""),
                name="unique_customer_phone_per_organization",
            )
        ]

    def __str__(self) -> str:
        return self.full_name


class Sale(models.Model):
    """
    One transaction at one till.

    ── IT BELONGS TO A SHIFT, NOT JUST TO A REGISTER ───────────────────────
    Blueprint §6 puts "open shift" first in the typical transaction, and §2
    makes the register level responsible for "cashier sessions, opening cash,
    transactions, payments, cash movements and closing reconciliation". A sale
    with no shift cannot be reconciled against a cash drawer at close, which
    is the entire point of counting one.

    `branch` is denormalised off the register deliberately. It is reachable
    through register → branch, but every report in the blueprint's §12 asks
    questions by branch, and the isolation map in identity/scoping.py resolves
    a `branch` reference in one hop instead of two.
    """

    class OrderType(models.TextChoices):
        """
        How this order is being served.

        COUNTER is the retail default and means "handed over here" — what
        every sale was before this existed, so old rows read as a fact rather
        than as an absence.
        """

        COUNTER = "counter", "At the counter"
        DINE_IN = "dine_in", "Dine in"
        TAKE_AWAY = "take_away", "Take away"
        DELIVERY = "delivery", "Delivery"

    class Status(models.TextChoices):
        # Blueprint module 1 lists "held sales" — a cart parked while the
        # customer fetches another item, not yet a financial record.
        HELD = "held", "Held"
        COMPLETED = "completed", "Completed"
        # Cancelled before it ever counted. Distinct from a refund: a void
        # says this never happened, a refund says it happened and was undone.
        VOIDED = "voided", "Voided"

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.PROTECT, related_name="sales"
    )
    branch = models.ForeignKey(
        Branches, on_delete=models.PROTECT, related_name="sales"
    )
    register = models.ForeignKey(
        Register, on_delete=models.PROTECT, related_name="sales"
    )
    shift = models.ForeignKey(
        RegisterShift, on_delete=models.PROTECT, related_name="sales"
    )
    cashier = models.ForeignKey(
        OrganizationStaff, on_delete=models.PROTECT, related_name="sales"
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name="sales",
        null=True,
        blank=True,
    )

    # Human-facing, per organisation. See the module docstring.
    number = models.PositiveIntegerField()

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.HELD
    )

    # ── THE STORED TOTALS ───────────────────────────────────────────────────
    # subtotal is before discount and before tax. total is what was owed.
    subtotal = models.DecimalField(**MONEY, default=ZERO)
    discount_total = models.DecimalField(**MONEY, default=ZERO)
    tax_total = models.DecimalField(**MONEY, default=ZERO)
    total = models.DecimalField(**MONEY, default=ZERO)

    # ── HOW IT IS BEING SERVED ──────────────────────────────────────────────
    #
    # Both are copied onto the sale like the prices are, never joined to. A
    # table renamed next month must not rewrite what an old receipt said, and
    # a shop that switches sector must not retroactively turn its counter
    # sales into dine-ins.
    order_type = models.CharField(
        max_length=16,
        choices=OrderType.choices,
        default=OrderType.COUNTER,
    )
    table_name = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text=(
            "Whatever the restaurant calls it — Table 4, Terrace 2, Bar. Free "
            "text rather than a foreign key: a floor plan is a bigger thing "
            "than this one field."
        ),
    )

    # ── IDEMPOTENCY — BLUEPRINT §11 ─────────────────────────────────────────
    #
    # "Conflict handling, idempotency keys and transaction identifiers should
    # be defined before production rollout."
    #
    # A till on a bad link sends a checkout, times out waiting, and retries.
    # Without a key the customer is charged twice and the stock is decremented
    # twice, and the only evidence is two identical sales a second apart —
    # which is also what two customers buying the same thing looks like.
    #
    # The key is the till's, generated before the first attempt and reused on
    # every retry of the SAME transaction. Unique per organisation; blank for
    # sales that never crossed a network (the admin, a fixture).
    idempotency_key = models.CharField(max_length=64, blank=True)

    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="unique_sale_number_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_sale_idempotency_key_per_organization",
            ),
        ]
        indexes = [
            # The two questions every report in §12 asks.
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["branch", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"Sale #{self.number}"

    @property
    def amount_paid(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), ZERO)

    @property
    def amount_refunded(self) -> Decimal:
        """Across every refund against this sale, which may be several."""
        return sum(
            (r.total for r in self.refunds.all() if r.status == Refund.Status.COMPLETED),
            ZERO,
        )

    @property
    def refundable_total(self) -> Decimal:
        return self.total - self.amount_refunded

    def clean(self):
        # The three have to agree or the sale is filed under a branch it did
        # not happen at, and every branch report quietly drifts.
        if self.register_id and self.branch_id:
            if self.register.branch_id != self.branch_id:
                raise ValidationError(
                    {"register": "That register is not at this branch."}
                )
        if self.shift_id and self.register_id:
            if self.shift.register_id != self.register_id:
                raise ValidationError(
                    {"shift": "That shift belongs to a different register."}
                )


class SaleItem(models.Model):
    """
    One line. Every commercial fact on it is a copy — see the module docstring.

    `product` is kept as a foreign key for reporting ("what sold this week"),
    and it is PROTECT rather than CASCADE: deleting a product must not delete
    the history of having sold it. The line stands on its own copied values if
    the product is ever renamed out of recognition.
    """

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        CatalogCategoryProduct, on_delete=models.PROTECT, related_name="sale_items"
    )

    # Copied at the moment of sale.
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=100, blank=True)
    note = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text=(
            "What the customer asked for on this line — no onions, medium, "
            "extra hot. Snapshotted like the price, so a receipt reprinted "
            "next year still says what was ordered."
        ),
    )
    unit_price = models.DecimalField(**MONEY)
    unit_cost = models.DecimalField(
        **MONEY,
        default=ZERO,
        help_text=(
            "Cost price at the time of sale. Copied so margin reporting "
            "reflects what the stock actually cost, not what it costs now."
        ),
    )

    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )

    discount_amount = models.DecimalField(**MONEY, default=ZERO)

    # The rate is copied as well as the amount. A receipt reprinted after the
    # VAT rate changes must show the rate that was charged.
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(**MONEY, default=ZERO)

    line_total = models.DecimalField(**MONEY, default=ZERO)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_name}"


class Payment(models.Model):
    """
    Money against a sale. Several rows on one sale IS a split payment —
    blueprint module 3 asks for "cash, M-Pesa, card, bank, credit and split
    payments with transaction references", and split needs no model of its own.

    Never updated after it is written. A payment taken in error is reversed by
    a refund, not by editing the row, for the reason in §10.
    """

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        MPESA = "mpesa", "M-Pesa"
        CARD = "card", "Card"
        BANK = "bank", "Bank transfer"
        # Puts the amount on the customer's balance instead of taking it now.
        # Requires a customer — enforced in services, where the message can
        # say why.
        CREDIT = "credit", "On account"

    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments")
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = models.DecimalField(**MONEY, validators=[MinValueValidator(Decimal("0.01"))])

    # M-Pesa code, card authorisation, bank slip. Blueprint module 3 asks for
    # "transaction references"; without one a disputed payment cannot be
    # traced back to the processor that took it.
    reference = models.CharField(max_length=100, blank=True)

    # Cash only. Stored rather than derived because the drawer is reconciled
    # against what was physically handed over, not against the total.
    tendered = models.DecimalField(**MONEY, null=True, blank=True)
    change_given = models.DecimalField(**MONEY, default=ZERO)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.get_method_display()} {self.amount}"


class Refund(models.Model):
    """
    A controlled reversal — blueprint module 9: "Controlled reversals without
    deleting original financial transactions."

    It is its own document with its own number, pointing at the sale. The
    example in §10 is exactly this shape:

        Sale   #1001   KSh 4,500
        Refund #2001  -KSh 1,500
        Original sale remains available for audit and reporting.

    Partial refunds are the normal case, so the lines are itemised: refunding
    one shirt from a sale of four returns one shirt to stock, not four.
    """

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        VOIDED = "voided", "Voided"

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.PROTECT, related_name="refunds"
    )
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="refunds")
    # Where the refund was processed, which is not necessarily where the sale
    # happened — a customer may return an item to a different branch.
    branch = models.ForeignKey(
        Branches, on_delete=models.PROTECT, related_name="refunds"
    )
    shift = models.ForeignKey(
        RegisterShift,
        on_delete=models.PROTECT,
        related_name="refunds",
        null=True,
        blank=True,
        help_text="The shift that processed it, when it was done at a till.",
    )
    processed_by = models.ForeignKey(
        OrganizationStaff, on_delete=models.PROTECT, related_name="refunds_processed"
    )

    number = models.PositiveIntegerField()
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.COMPLETED
    )

    total = models.DecimalField(**MONEY, default=ZERO)
    # Free text, required by services. "Why was this money given back" is the
    # first question an auditor asks and the one nobody remembers.
    reason = models.CharField(max_length=200)

    # Refunds are retried by a till on a bad link exactly like sales are.
    idempotency_key = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"],
                name="unique_refund_number_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_refund_idempotency_key_per_organization",
            ),
        ]

    def __str__(self) -> str:
        return f"Refund #{self.number}"


class RefundItem(models.Model):
    """
    What went back, line by line.

    `sale_item` rather than `product`: a sale may contain the same product on
    two lines at different prices (a discounted one and a full-price one), and
    refunding "one of them" has to say which, or the money returned is a guess.
    """

    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(
        SaleItem, on_delete=models.PROTECT, related_name="refund_items"
    )

    quantity = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    amount = models.DecimalField(**MONEY, default=ZERO)

    # ── DID IT COME BACK ON THE SHELF? ──────────────────────────────────────
    # A returned shirt is stock again; a returned half-eaten meal is not.
    # Without this the choice gets made silently, and inventory drifts from
    # the shelf in a way nobody can reconstruct.
    restocked = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]

    def __str__(self) -> str:
        return f"{self.quantity} × {self.sale_item.product_name} returned"


class Receipt(models.Model):
    """
    The proof handed over, and the record of every time it was handed over
    again — blueprint module 10: "Thermal/A4/PDF receipts, reprints and
    digital delivery."

    ── THE BACKEND OWNS THE NUMBER AND THE AUDIT, NOT THE PAPER ────────────
    Thermal vs A4 vs PDF is a rendering choice and belongs wherever the
    printing happens. What cannot live there is the reprint count: "this
    receipt has been printed four times" is how a duplicate-receipt refund
    fraud is noticed, and a counter kept by the till is a counter the till can
    reset.
    """

    sale = models.OneToOneField(Sale, on_delete=models.CASCADE, related_name="receipt")
    number = models.CharField(max_length=32)

    issued_at = models.DateTimeField(default=timezone.now)
    reprint_count = models.PositiveIntegerField(default=0)
    last_reprinted_at = models.DateTimeField(null=True, blank=True)

    # Where it was sent, if it was sent. Blank is the ordinary case: printed
    # and handed over.
    delivered_to = models.CharField(
        max_length=120,
        blank=True,
        help_text="Phone number or email a digital copy was sent to.",
    )

    def __str__(self) -> str:
        return self.number
