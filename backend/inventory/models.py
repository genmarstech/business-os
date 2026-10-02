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


# ═════════════════════════════════════════════════════════════════════════════
# COUNTING THE SHELVES
# ═════════════════════════════════════════════════════════════════════════════
#
# Five comments in this codebase justify how carefully a movement is written by
# appealing to a stock take — "a stock take six months later can walk every
# unit back", "the ones a stock take is reconciled against". Until now there
# was no stock take. The discipline was being paid for and never cashed in.
#
# This is the inventory half of what branches/services.py already does for
# cash: count the thing, compare it with what the system believed, record the
# variance, and close it so the number means something afterwards.


class StockCount(models.Model):
    """
    One session of counting a branch's shelves.

    ══════════════════════════════════════════════════════════════════════════
    ONE OPEN COUNT PER BRANCH, ENFORCED IN THE DATABASE.

    Two people counting the same shelves at once produce two contradictory
    truths and no way to tell which was first. The cash drawer avoids this by
    there being one drawer; shelves have no such luck, so the constraint does
    it.
    ══════════════════════════════════════════════════════════════════════════

    ── CLOSING IS NOT REVERSIBLE, FOR THE REASON close_shift IS NOT ───────────

    A count that can be reopened and recounted is a count whose variance means
    nothing: the second number is always the one that agrees. A miscount is
    corrected by counting again — a NEW count, which is its own record — and
    never by editing this one.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Counting"
        CLOSED = "closed", "Closed"
        # Started by accident, or abandoned half way. Kept rather than
        # deleted: "somebody began counting the back room on Tuesday and
        # stopped" is a thing a manager may need to see.
        ABANDONED = "abandoned", "Abandoned"

    organization = models.ForeignKey(
        "organisations.BusinessOrganization",
        on_delete=models.CASCADE,
        related_name="stock_counts",
    )
    # Denormalised like Sale.branch: a count belongs where it was taken, and
    # §8 scoping wants one hop rather than two.
    branch = models.ForeignKey(
        "branches.Branches", on_delete=models.PROTECT, related_name="stock_counts"
    )
    number = models.PositiveIntegerField()

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.OPEN, db_index=True
    )

    opened_by = models.ForeignKey(
        "organisations.OrganizationStaff",
        on_delete=models.PROTECT,
        related_name="stock_counts_opened",
    )
    opened_at = models.DateTimeField(auto_now_add=True)

    # Null while open. The person who closes is recorded separately from the
    # person who counted, because they are frequently and deliberately not the
    # same person — see the permission note in identity/access.py.
    closed_by = models.ForeignKey(
        "organisations.OrganizationStaff",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_counts_closed",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "number"], name="stockcount_number_per_org"
            ),
            # Partial, so closed and abandoned counts do not block the next
            # one. This is the rule in the docstring, in the only place it
            # cannot be forgotten.
            models.UniqueConstraint(
                fields=["branch"],
                condition=models.Q(status="open"),
                name="one_open_stockcount_per_branch",
            ),
        ]

    def __str__(self) -> str:
        return f"Count {self.number} at {self.branch_id}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN


class StockCountLine(models.Model):
    """
    One product counted, and what the system thought at that moment.

    ══════════════════════════════════════════════════════════════════════════
    `expected_quantity` IS SNAPSHOTTED WHEN THE LINE IS COUNTED, NOT WHEN THE
    COUNT IS OPENED, AND NOT READ LIVE AT CLOSE.

    A shop keeps trading while somebody counts it. Reading the expected figure
    at close would fold every sale made during the count into the variance,
    and the number would describe the afternoon's trade rather than the
    discrepancy. Snapshotting at open has the same fault from the other end.

    Taken at the instant of counting, the pair is true: this is what was on
    the shelf and this is what the system believed, both at one moment. Sales
    after that are real movements and apply on top.

    It is the same rule as every price on a document line in this codebase —
    a copy, never a join.
    ══════════════════════════════════════════════════════════════════════════
    """

    count = models.ForeignKey(
        StockCount, on_delete=models.CASCADE, related_name="lines"
    )
    inventory = models.ForeignKey(
        BranchInventory, on_delete=models.PROTECT, related_name="count_lines"
    )

    expected_quantity = models.DecimalField(max_digits=12, decimal_places=2)
    counted_quantity = models.DecimalField(max_digits=12, decimal_places=2)

    counted_by = models.ForeignKey(
        "organisations.OrganizationStaff",
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
    )
    counted_at = models.DateTimeField(auto_now=True)
    note = models.TextField(blank=True, default="")

    # Written when the count closes and the difference is booked, so a line
    # can be traced to the movement it caused. Null on a line that agreed —
    # nothing moved, so there is nothing to point at.
    movement = models.ForeignKey(
        StockMovement,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="count_lines",
    )

    class Meta:
        ordering = ["inventory__product__name"]
        constraints = [
            # Counting the same shelf twice in one session is a correction,
            # not a second line. `record_count` updates in place.
            models.UniqueConstraint(
                fields=["count", "inventory"], name="one_line_per_product_per_count"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.inventory_id}: counted {self.counted_quantity}"

    @property
    def variance(self):
        """Positive is a surplus on the shelf, negative is a shortfall."""
        return self.counted_quantity - self.expected_quantity
