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



