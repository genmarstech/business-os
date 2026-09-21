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



class TaxRule(models.Model):
    """
    A rate the organisation charges — blueprint §9 puts this in Catalog, and
    §7 lists "tax configuration" as organization-owned.

    ── INCLUSIVE VS EXCLUSIVE IS NOT A DISPLAY SETTING ─────────────────────
    In Kenya a shelf price is normally VAT-inclusive: KSh 100 on the label
    means the customer pays 100 and 13.79 of it is tax. Exclusive means the
    till adds 16% and the customer pays 116. Getting this backwards does not
    look like a bug — it looks like a shop that has been quietly underpaying
    or overcharging VAT — so it is a field on the rule rather than a global
    assumption, and sales.services reads it rather than guessing.

    The rate is stored, and SaleItem copies it at the moment of sale. Changing
    a rate must never rewrite what a customer was charged last month.
    """

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="tax_rules"
    )

    name = models.CharField(max_length=60, help_text='e.g. "VAT 16%" or "Zero rated"')
    rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="A percentage. 16.00 means 16%, not 1600%.",
    )
    is_inclusive = models.BooleanField(
        default=True, help_text="True when the shelf price already contains the tax."
    )

    # Applied to any product that names no rule of its own. Exactly one per
    # organisation, enforced below — two defaults is a coin toss at the till.
    is_default = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_tax_rule_name_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_default=True),
                name="one_default_tax_rule_per_organization",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.rate}%)"


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

    # ── WHAT A SCANNER SENDS ────────────────────────────────────────────────
    #
    # Blueprint module 1 opens with "barcode/search" — a cashier scans, and
    # the till has one shot at resolving what was scanned into a product.
    # Separate from `sku`, which is the shop's own code: an EAN belongs to the
    # manufacturer and two shops selling the same Coca-Cola scan the same
    # digits. Unique per organisation only, like everything else here.
    #
    # Blank is ordinary — loose goods, services and anything sold by weight
    # have no barcode at all, so the uniqueness constraint excludes empties.
    barcode = models.CharField(max_length=64, blank=True)

    # Falls back to the organisation's default rule when unset. Nullable
    # rather than required because a shop configures tax once and should not
    # be blocked from adding a product before it has.
    tax_rule = models.ForeignKey(
        "catalog.TaxRule",
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
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
            models.UniqueConstraint(
                fields=["organization", "barcode"],
                condition=~models.Q(barcode=""),
                name="unique_product_barcode_per_organization",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.organization} - {self.name}"