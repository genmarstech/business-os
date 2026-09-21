from django.db import models
from organisations.models import BusinessOrganization


# Create your models here.
class CatalogCategories(models.Model):
    organization = models.ForeignKey(BusinessOrganization, on_delete=models.CASCADE, related_name='categories')

    # Per organisation. Globally unique meant one shop naming a category
    # "Beverages" stopped every other shop from doing the same — and told them
    # so. CatalogCategoryProduct below already got this right; this did not.
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'name'],
                name='unique_category_name_per_organization',
            )
        ]

    def __str__(self):
        return f"{self.organization} at {self.name}"



class CatalogCategoryProduct(models.Model):
    organization = models.ForeignKey(
        BusinessOrganization,
        on_delete=models.CASCADE,
        related_name="products"
    )

    category = models.ForeignKey(
        CatalogCategories,
        on_delete=models.PROTECT,
        related_name="products"
    )

    name = models.CharField(
        max_length=200
    )

    description = models.TextField(
        blank=True
    )

    sku = models.CharField(
        max_length=100
    )

    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2
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
                fields=["organization", "name"],
                name="unique_product_name_per_organization"
            ),
            models.UniqueConstraint(
                fields=["organization", "sku"],
                name="unique_product_sku_per_organization"
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.organization} - {self.name}"