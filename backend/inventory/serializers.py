from .models import (
    BranchInventory,
    StockAdjustment,
    StockCount,
    StockCountLine,
    StockLevel,
    StockMovement,
    StockTransfer,
)
from branches.models import Branches
from organisations.models import OrganizationStaff
from rest_framework import serializers

# relevant serializers

class BranchInventorySerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(
        source="branch.branch_name",
        read_only=True
    )

    product_name = serializers.CharField(
        source="product.name",
        read_only=True
    )

    product_sku = serializers.CharField(
        source="product.sku",
        read_only=True
    )

    class Meta:
        model = BranchInventory
        fields = [
            "id",
            "branch",
            "branch_name",
            "product",
            "product_name",
            "product_sku",
            "quantity",
            "reorder_level",
            "is_active",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "branch_name",
            "product_name",
            "product_sku",
            "created_at",
            "updated_at",
        ]

    def validate_quantity(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Quantity cannot be negative."
            )

        return value

    def validate_reorder_level(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Reorder level cannot be negative."
            )

        return value

class StockMovementSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(
        source="inventory.branch.branch_name",
        read_only=True
    )

    product_name = serializers.CharField(
        source="inventory.product.name",
        read_only=True
    )

    product_sku = serializers.CharField(
        source="inventory.product.sku",
        read_only=True
    )

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "inventory",
            "branch_name",
            "product_name",
            "product_sku",
            "movement_type",
            "quantity_before",
            "quantity_after",
            "reference",
            "reason",
            "created_at",
        ]

        read_only_fields = [
            "id",
            "branch_name",
            "product_name",
            "product_sku",
            "quantity_before",
            "quantity_after",
            "created_at",
        ]


class StockTransferSerializer(serializers.ModelSerializer):
    from_branch_name = serializers.CharField(
        source="from_branch.branch_name",
        read_only=True
    )

    to_branch_name = serializers.CharField(
        source="to_branch.branch_name",
        read_only=True
    )

    product_name = serializers.CharField(
        source="product.name",
        read_only=True
    )

    product_sku = serializers.CharField(
        source="product.sku",
        read_only=True
    )

    class Meta:
        model = StockTransfer
        fields = [
            "id",
            "from_branch",
            "from_branch_name",
            "to_branch",
            "to_branch_name",
            "product",
            "product_name",
            "product_sku",
            "quantity",
            "status",
            "notes",
            "transfer_date",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "from_branch_name",
            "to_branch_name",
            "product_name",
            "product_sku",
            "status",
            "transfer_date",
            "updated_at",
        ]

    def validate(self, attrs):
        from_branch = attrs.get("from_branch")
        to_branch = attrs.get("to_branch")
        quantity = attrs.get("quantity")

        if from_branch and to_branch and from_branch == to_branch:
            raise serializers.ValidationError(
                "The source and destination branches cannot be the same."
            )

        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError(
                "Transfer quantity must be greater than zero."
            )

        return attrs


class StockAdjustmentSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(
        source="inventory.branch.branch_name",
        read_only=True
    )

    product_name = serializers.CharField(
        source="inventory.product.name",
        read_only=True
    )

    product_sku = serializers.CharField(
        source="inventory.product.sku",
        read_only=True
    )

    class Meta:
        model = StockAdjustment
        fields = [
            "id",
            "inventory",
            "branch_name",
            "product_name",
            "product_sku",
            "adjustment_type",
            "quantity_before",
            "quantity_after",
            "reason",
            "created_at",
            "updated_at",
        ]

        read_only_fields = [
            "id",
            "branch_name",
            "product_name",
            "product_sku",
            "quantity_before",
            "quantity_after",
            "created_at",
            "updated_at",
        ]


class StockLevelSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(
        source="inventory.branch.branch_name",
        read_only=True
    )

    product_name = serializers.CharField(
        source="inventory.product.name",
        read_only=True
    )

    product_sku = serializers.CharField(
        source="inventory.product.sku",
        read_only=True
    )

    class Meta:
        model = StockLevel
        fields = [
            "id",
            "inventory",
            "branch_name",
            "product_name",
            "product_sku",
            "quantity",
            "recorded_at",
        ]

        read_only_fields = [
            "id",
            "branch_name",
            "product_name",
            "product_sku",
            "quantity",
            "recorded_at",
        ]

# ── counting the shelves ─────────────────────────────────────────────────────


class StockCountLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="inventory.product.name", read_only=True)
    product_sku = serializers.CharField(source="inventory.product.sku", read_only=True)
    counted_by_name = serializers.CharField(source="counted_by.full_name", read_only=True)
    # Derived, never stored and never writable. A variance a client could
    # state is a variance a client could state wrongly.
    variance = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = StockCountLine
        fields = [
            "id", "inventory", "product_name", "product_sku",
            "expected_quantity", "counted_quantity", "variance",
            "counted_by", "counted_by_name", "counted_at", "note", "movement",
        ]
        read_only_fields = fields


class StockCountSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.branch_name", read_only=True)
    opened_by_name = serializers.CharField(source="opened_by.full_name", read_only=True)
    closed_by_name = serializers.CharField(
        source="closed_by.full_name", read_only=True, default=None
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    lines = StockCountLineSerializer(many=True, read_only=True)
    summary = serializers.SerializerMethodField()

    class Meta:
        model = StockCount
        fields = [
            "id", "number", "organization", "branch", "branch_name",
            "status", "status_label",
            "opened_by", "opened_by_name", "opened_at",
            "closed_by", "closed_by_name", "closed_at",
            "note", "lines", "summary",
        ]
        read_only_fields = fields

    def get_summary(self, count) -> dict:
        from . import services

        return services.count_summary(count)


class OpenCountSerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branches.objects.all())
    opened_by = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")


class RecordCountSerializer(serializers.Serializer):
    inventory = serializers.PrimaryKeyRelatedField(
        queryset=BranchInventory.objects.all()
    )
    counted = serializers.DecimalField(max_digits=12, decimal_places=2)
    counted_by = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CloseCountSerializer(serializers.Serializer):
    closed_by = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )


class AbandonCountSerializer(serializers.Serializer):
    closed_by = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    # Required, unlike the note on a close. See services.abandon_count.
    reason = serializers.CharField(max_length=300)
