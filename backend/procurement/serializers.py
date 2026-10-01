"""
Shapes in and out. The writing itself happens in services.py.

── WHY `total`, `quantity_received` AND EVERY NUMBER IS READ-ONLY ──────────────

They are derived. A client that could send `total` could send an order whose
total disagrees with its own lines, and a client that could send
`quantity_received` could close an order nobody delivered. Both are figures the
business is measured on, so both are computed from the rows beneath them.

The same note sales/serializers.py carries about a writable sale total, for the
same reason.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)


class SupplierSerializer(serializers.ModelSerializer):
    # `organization` is writable, as it is on every other organisation-level
    # model here. It is not trusted: identity/scoping.py refuses a write
    # naming a tenant the caller cannot see, with the same wording it uses for
    # a record that does not exist. A client naming a stranger's organisation
    # gets "no such record", which is all it is entitled to learn.
    # ── DECLARED BECAUSE THE CONSTRAINT MAKES DRF DEMAND IT ────────────────
    #
    # `(organization, phone_number)` is unique where the phone is not blank,
    # and DRF turns any unique-together into "this field is required" on every
    # field it names. A supplier the shop only has an email for would have
    # been unaddable — the constraint exists to catch a duplicate, not to
    # insist on a number.
    phone_number = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )

    class Meta:
        model = Supplier
        fields = [
            "id",
            "organization",
            "name",
            "contact_person",
            "phone_number",
            "email",
            "address",
            "kra_pin",
            "lead_time_days",
            "note",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("A supplier needs a name.")
        return name


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    """
    One line, both directions.

    Writing needs `product` and `quantity_ordered`; `unit_cost` is optional
    and falls back to the product's current cost in the service. Everything
    else is a copy the service stamps on, or a figure it maintains.
    """

    outstanding = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    unit_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False
    )

    class Meta:
        model = PurchaseOrderItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_sku",
            "quantity_ordered",
            "quantity_received",
            "outstanding",
            "unit_cost",
            "line_total",
        ]
        read_only_fields = [
            "id",
            "product_name",
            "product_sku",
            "quantity_received",
            "outstanding",
            "line_total",
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """
    The order and its lines, in one document.

    Nested on purpose: an order without its lines is not an order, and two
    round trips to create one leaves a window where a half-written order
    exists. `services.raise_order` writes both inside a transaction.
    """

    items = PurchaseOrderItemSerializer(many=True)

    # Declared for the reason SupplierSerializer.phone_number is: the
    # conditional unique constraint on (organization, idempotency_key) makes
    # DRF mark the key required, and an order raised from a screen that has no
    # retry story should not have to invent one.
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )

    organization = serializers.PrimaryKeyRelatedField(read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    branch_name = serializers.CharField(source="branch.branch_name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    raised_by_name = serializers.CharField(read_only=True)
    approved_by_name = serializers.CharField(read_only=True)
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = [
            "id",
            "organization",
            "branch",
            "branch_name",
            "supplier",
            "supplier_name",
            "number",
            "status",
            "status_label",
            "is_editable",
            "expected_at",
            "note",
            "total",
            "items",
            "raised_by_name",
            "approved_by_name",
            "approved_at",
            "cancelled_at",
            "cancelled_reason",
            "idempotency_key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "number",
            "status",
            "status_label",
            "is_editable",
            "total",
            "branch_name",
            "supplier_name",
            "raised_by_name",
            "approved_by_name",
            "approved_at",
            "cancelled_at",
            "cancelled_reason",
            "created_at",
            "updated_at",
        ]

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("An order needs at least one product.")
        return value


class ReceiptLineSerializer(serializers.Serializer):
    """One line of a delivery: which order line, and how many arrived."""

    # Resolved out of the whole table, then checked against the order the
    # action was called on — the service refuses a line belonging to another
    # order, which is also what stops one belonging to another tenant.
    item = serializers.PrimaryKeyRelatedField(queryset=PurchaseOrderItem.objects.all())
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReceiveSerializer(serializers.Serializer):
    lines = ReceiptLineSerializer(many=True)
    delivery_note = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )
    note = serializers.CharField(required=False, allow_blank=True)
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True
    )

    def validate_lines(self, value):
        if not value:
            raise serializers.ValidationError("Say what arrived.")
        return value


class GoodsReceiptItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(
        source="order_item.product_name", read_only=True
    )
    product_sku = serializers.CharField(
        source="order_item.product_sku", read_only=True
    )

    class Meta:
        model = GoodsReceiptItem
        fields = [
            "id",
            "order_item",
            "product_name",
            "product_sku",
            "quantity",
            "unit_cost",
        ]
        read_only_fields = fields


class GoodsReceiptSerializer(serializers.ModelSerializer):
    items = GoodsReceiptItemSerializer(many=True, read_only=True)
    branch_name = serializers.CharField(source="branch.branch_name", read_only=True)
    supplier_name = serializers.CharField(
        source="purchase_order.supplier.name", read_only=True
    )
    order_number = serializers.IntegerField(
        source="purchase_order.number", read_only=True
    )
    received_by_name = serializers.CharField(read_only=True)

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "organization",
            "branch",
            "branch_name",
            "purchase_order",
            "order_number",
            "supplier_name",
            "number",
            "delivery_note",
            "note",
            "received_by_name",
            "received_at",
            "items",
            "created_at",
        ]
        read_only_fields = fields


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
