from django.db import models
from organisations.models import BusinessOrganization
from branches.models import Branches
from catalog.models import CatalogCategoryProduct

# Create your models here.

class BranchInventory(models.Model):
    branch = models.ForeignKey(Branches, on_delete=models.CASCADE, related_name='inventory')
    product = models.ForeignKey(CatalogCategoryProduct, on_delete=models.PROTECT, related_name='products_inventory')
    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "product"],
                name="unique_product_per_branch"
            )
        ]

    def __str__(self):
        return f"{self.branch} - {self.product}"
    
class StockMovement(models.Model):
    MOVEMENT_TYPES = [
        ('PURCHASE', 'Purchase'),
        ('SALE', 'Sale'),
        ('TRANSFER_IN', 'Transfer In'),
        ('TRANSFER_OUT', 'Transfer Out'),
        ('ADJUSTMENT', 'Adjustment'),
        ('RETURN', 'Return'),
        ('DAMAGE', 'Damage'),
        ('THEFT', 'Theft'),
        ('OTHER', 'Other'),
    ]

    inventory = models.ForeignKey(BranchInventory, on_delete=models.PROTECT, related_name='stock_movements')
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPES)
    quantity_before = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=255, blank=True, null=True)
    reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.inventory} - {self.movement_type} - {self.quantity_after}"


class StockTransfer(models.Model):

    from_branch = models.ForeignKey(Branches, on_delete=models.PROTECT, related_name='outgoing_transfers')
    to_branch = models.ForeignKey(Branches, on_delete=models.PROTECT, related_name='incoming_transfers')
    product = models.ForeignKey(CatalogCategoryProduct, on_delete=models.PROTECT, related_name='transferred_products')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=[
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ], default='PENDING')
    notes = models.TextField(blank=True, null=True)
    transfer_date = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.from_branch} to {self.to_branch} - {self.product} - {self.quantity}"

class StockAdjustment(models.Model):
    inventory = models.ForeignKey(BranchInventory, on_delete=models.PROTECT, related_name='stock_adjustments')
    adjustment_type = models.CharField(max_length=20, choices=[
        ('INCREASE', 'Increase'),
        ('DECREASE', 'Decrease'),
    ])
    quantity_before = models.DecimalField(max_digits=12, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.inventory} - {self.adjustment_type} - {self.quantity_after}"

class StockLevel(models.Model):
    inventory = models.ForeignKey(BranchInventory, on_delete=models.PROTECT, related_name='stock_levels')
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.inventory} - {self.quantity} - {self.recorded_at}"
