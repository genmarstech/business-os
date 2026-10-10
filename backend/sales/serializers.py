"""
Shapes in and out. No business rules — those are in services.py.

── READ SERIALISERS ARE NOT WRITE SERIALISERS HERE ─────────────────────────────

A Sale is written by `services.checkout` and never by a ModelSerializer, so
SaleSerializer is read-only on every derived field. Leaving `total` writable
would mean a till could name its own total, and the one thing a point of sale
must never accept from the client is what the customer owes.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from branches.models import Branches, RegisterShift
from catalog.models import CatalogCategoryProduct, TaxRule
from organisations.models import OrganizationStaff

from payments.models import StkPush

from .models import Customer, Payment, Receipt, Refund, RefundItem, Sale, SaleItem


class TaxRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRule
        fields = [
            "id", "organization", "name", "rate", "is_inclusive",
            "is_default", "is_active", "created_at",
        ]
        read_only_fields = ["created_at"]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id", "organization", "full_name", "phone_number", "email",
            "credit_balance", "is_active", "created_at", "updated_at",
        ]
        # The balance is maintained by services from actual payments and
        # refunds. Writable, it would be whatever the client last sent.
        read_only_fields = ["credit_balance", "created_at", "updated_at"]


class SaleItemSerializer(serializers.ModelSerializer):
    """
    ── THE TWO RETURN FIGURES ARE DERIVED, NEVER STORED ────────────────────
    `services.refund_sale` works out what is still refundable by adding up the
    refund items already written, and says why: a counter on the line would be
    a second source of truth, and three partial returns of one shirt each
    must not be able to send four back. A client needs the same answer BEFORE
    it posts — a till that caps a quantity box at what was sold lets a cashier
    type 3 against a line with 1 left and learn about it from a refusal, with
    a customer watching.

    So this computes it the same way from the same rows rather than keeping a
    number in step with them. `SaleViewSet` prefetches `items__refund_items
    __refund` to keep it off the database per line.
    """

    quantity_refunded = serializers.SerializerMethodField()
    quantity_refundable = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = [
            "id", "product", "product_name", "sku", "note", "unit_price", "quantity",
            "discount_amount", "tax_rate", "tax_amount", "line_total",
            "quantity_refunded", "quantity_refundable",
        ]
        read_only_fields = fields

    def _refunded(self, item) -> Decimal:
        # VOIDED refunds do not count, exactly as in refund_sale: a reversal
        # that was itself reversed sent nothing back.
        return sum(
            (
                row.quantity
                for row in item.refund_items.all()
                if row.refund.status == Refund.Status.COMPLETED
            ),
            Decimal("0.00"),
        )

    def get_quantity_refunded(self, item) -> str:
        return f"{self._refunded(item):.2f}"

    def get_quantity_refundable(self, item) -> str:
        return f"{item.quantity - self._refunded(item):.2f}"


class PaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id", "method", "method_label", "amount", "reference",
            "tendered", "change_given", "created_at",
        ]
        read_only_fields = fields


class ReceiptSerializer(serializers.ModelSerializer):
    class Meta:
        model = Receipt
        fields = [
            "id", "number", "issued_at", "reprint_count",
            "last_reprinted_at", "delivered_to",
        ]
        read_only_fields = fields


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    receipt = ReceiptSerializer(read_only=True)
    amount_refunded = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "number", "status", "status_label", "order_type", "table_name",
            "organization", "branch",
            "register", "shift", "cashier", "customer", "subtotal",
            "discount_total", "tax_total", "total", "amount_refunded",
            "items", "payments", "receipt", "void_reason", "voided_at",
            "completed_at", "created_at",
        ]
        read_only_fields = fields


# ── the checkout request ────────────────────────────────────────────────────
#
# A plain Serializer, not a ModelSerializer. A checkout is not "create a Sale
# row"; it is an operation over several tables with rules of its own, and
# modelling it as a row would invite exactly the writable `total` this module's
# docstring warns about.


class CheckoutLineSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=CatalogCategoryProduct.objects.all()
    )
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, default=0
    )
    note = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=200
    )


class CheckoutPaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    reference = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    # ── A CONFIRMED M-PESA PUSH, WHERE THERE IS ONE ─────────────────────
    #
    # Sent by the till after `/pay/mpesa/pushes/{id}/check/` has come back
    # paid. `services.checkout` spends it INSIDE the same transaction that
    # writes the sale: if the two were separate calls, a sale could be
    # written while the push stayed unspent, and the same confirmed payment
    # would then be available to fund a second basket.
    #
    # Unscoped queryset on purpose — the service checks the organisation,
    # the amount and whether it has already been spent, and says so in
    # words. A scoped queryset here would turn all three into "that is not
    # a valid choice".
    stk_push = serializers.PrimaryKeyRelatedField(
        queryset=StkPush.objects.all(), required=False, allow_null=True
    )


class CheckoutSerializer(serializers.Serializer):
    shift = serializers.PrimaryKeyRelatedField(queryset=RegisterShift.objects.all())
    # How it is being served. Absent means counter, which is what every retail
    # sale is and what every sale was before hospitality existed.
    order_type = serializers.CharField(required=False, allow_blank=True, default="")
    table_name = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=40
    )
    cashier = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all(), required=False, allow_null=True
    )
    lines = CheckoutLineSerializer(many=True)
    payments = CheckoutPaymentSerializer(many=True)

    # Blueprint §11. The till generates this before its first attempt and
    # reuses it on every retry of the same transaction; the server returns the
    # original sale rather than writing a second one.
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )


class RefundLineSerializer(serializers.Serializer):
    sale_item = serializers.PrimaryKeyRelatedField(queryset=SaleItem.objects.all())
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)
    # Defaults to true: most returns go back on the shelf. See RefundItem.
    restock = serializers.BooleanField(required=False, default=True)


class RefundRequestSerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branches.objects.all())
    processed_by = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    shift = serializers.PrimaryKeyRelatedField(
        queryset=RegisterShift.objects.all(), required=False, allow_null=True
    )
    lines = RefundLineSerializer(many=True)
    reason = serializers.CharField(max_length=200)
    # Required, with no default. The banner on `Refund.method` says why a
    # sensible-looking default here would be a drawer that reads short on
    # every M-Pesa reversal.
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )


class RefundItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(
        source="sale_item.product_name", read_only=True
    )

    class Meta:
        model = RefundItem
        fields = ["id", "sale_item", "product_name", "quantity", "amount", "restocked"]
        read_only_fields = fields


class RefundSerializer(serializers.ModelSerializer):
    items = RefundItemSerializer(many=True, read_only=True)
    sale_number = serializers.IntegerField(source="sale.number", read_only=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    processed_by_name = serializers.CharField(
        source="processed_by.full_name", read_only=True
    )

    class Meta:
        model = Refund
        fields = [
            "id", "number", "status", "organization", "sale", "sale_number",
            "branch", "shift", "processed_by", "processed_by_name",
            "method", "method_label", "total", "reason", "items",
            "created_at",
        ]
        read_only_fields = fields


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=200)


class ReprintSerializer(serializers.Serializer):
    delivered_to = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )
