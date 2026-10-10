from decimal import ROUND_HALF_UP, Decimal

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

    # ══════════════════════════════════════════════════════════════════
    # BOUGHT BY THE CARTON, SOLD BY THE BOTTLE.
    #
    # A shop orders ten cartons of soda and a customer buys one bottle.
    # Without this the two quantities are the same number in the same
    # column, so receiving ten cartons put TEN on the shelf and the till
    # ran out after ten bottles while the storeroom was full.
    #
    # `units_per_pack` is how many sellable units come in one bought pack.
    # It defaults to 1, which is the truth for most lines — bread, a bar of
    # soap, anything bought the way it is sold — and it is what every
    # existing product means, so the migration needs no data step and
    # nothing about a shop that does not use this changes.
    #
    # ⚠ cost_price AND selling_price ARE BOTH PER UNIT. Always, including
    #   for a product bought by the carton. The pack price lives on the
    #   purchase order, where it is what the supplier charges; putting a
    #   pack price in either of these would make every margin, every
    #   report and every receipt line wrong by a factor of the pack size.
    # ══════════════════════════════════════════════════════════════════
    units_per_pack = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("1"),
        help_text=(
            "How many sellable units come in one bought pack. 24 for a "
            "carton of 24 bottles. Leave at 1 for anything bought the way "
            "it is sold."
        ),
    )
    # Words for the buyer's screen, not a unit of account. Nothing computes
    # with this — `units_per_pack` is the number — so a shop that calls it a
    # "crate" and a shop that calls it a "box" are not two code paths.
    pack_name = models.CharField(
        max_length=40,
        blank=True,
        help_text="What one pack is called: carton, crate, sack, dozen.",
    )

    # Per unit. See the banner above.
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    # Per unit. See the banner above.
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
            # A pack of zero divides by nothing when a delivery works out
            # what landed on the shelf, and a negative pack takes stock away
            # when a lorry arrives. Refused at the database, because the one
            # that matters is the row nobody validated.
            models.CheckConstraint(
                condition=models.Q(units_per_pack__gt=0),
                name="a_pack_holds_at_least_something",
            ),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.organization} - {self.name}"

    @property
    def is_bulk(self) -> bool:
        """Bought differently from how it is sold."""
        return self.units_per_pack != Decimal("1")

    def units_in(self, packs: Decimal) -> Decimal:
        """
        How many sellable units arrive when `packs` are delivered.

        One place, so a buyer's screen, a goods receipt and a stock figure
        cannot each round it their own way.
        """
        return Decimal(packs) * self.units_per_pack

    def unit_cost_from_pack(self, pack_cost: Decimal) -> Decimal:
        """
        What one unit cost, given what the supplier charged for a pack.

        Quantised to the money the rest of the system uses. A carton of 24
        at 1,000 is 41.666… a bottle, and carrying that into a margin makes
        every total disagree with the sum of its lines by fractions of a
        cent — which is the shape of error nobody finds for months.
        """
        return (Decimal(pack_cost) / self.units_per_pack).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

# ══════════════════════════════════════════════════════════════════════════════
# PRICE LISTS — blueprint §9, the `ProductPrice` the Catalog domain was missing
# ══════════════════════════════════════════════════════════════════════════════
#
# A shop charges different prices in different places and at different times:
# a branch in a wealthier suburb, a wholesale rate for somebody buying a case,
# a fortnight of promotional pricing. Until now there was exactly one number —
# `CatalogCategoryProduct.selling_price` — and the only way to run a promotion
# was to edit it and remember to edit it back.
#
# ── THE OVERRIDE NEVER REMOVES A PRICE ─────────────────────────────────────
#
# `selling_price` on the product remains the answer whenever no list says
# otherwise. A price list adds an override for the products it names and is
# silent about everything else, so a promotion listing three items cannot make
# the other four hundred unsellable. There is deliberately no way for a price
# list to withdraw a product from sale: that is `is_active` on the product, it
# is one switch, and splitting it across two concepts is how a shop ends up
# unable to work out why something will not scan.
#
# ── AND IT CHANGES WHAT IS OFFERED, NEVER WHAT WAS CHARGED ─────────────────
#
# Every price on a sale line is a copy, as it has always been. Editing a list
# changes tomorrow's prices and rewrites nothing — the same snapshot rule that
# keeps a supplier's price rise out of last quarter's margin.


class PriceList(models.Model):
    """
    A named set of prices, in force somewhere, sometimes.

    ── PRECEDENCE IS UNIQUE PER ORGANISATION, AND THAT IS THE WHOLE DESIGN ─

    Two lists can easily both apply: a branch's own prices and a promotion
    running everywhere. Something has to decide, and "something" must not be
    whatever order the database felt like returning rows in — a till and a
    receipt printed a second apart would disagree, and the shop would have no
    way to find out why.

    So precedence is an integer, it is UNIQUE per organisation at the database
    level, and resolution walks lists from the highest down. A tie cannot be
    stored, so a tie cannot be broken arbitrarily at read time. Setting one up
    is a conversation somebody has once, when they create the list, rather
    than a mystery somebody has at the counter.

    ── IT APPLIES AT THE BRANCHES NAMED, OR EVERYWHERE IF NONE ARE ────────

    Through `PriceListBranch` rather than a single nullable foreign key,
    because "this promotion runs at Westlands and Karen but not Kisumu" is an
    ordinary thing to want and a nullable column cannot say it.
    """

    organization = models.ForeignKey(
        "organisations.BusinessOrganization",
        on_delete=models.CASCADE,
        related_name="price_lists",
    )

    name = models.CharField(max_length=120)
    note = models.TextField(blank=True)

    # ── THE WINDOW. BOTH ENDS OPTIONAL, BOTH ENDS INCLUSIVE ────────────────
    #
    # Dates rather than timestamps: a promotion runs "the whole of Friday" in
    # the shop's own day, and a shop three hours east of the server must not
    # lose an evening of it. Inclusive at both ends, because "ends on the
    # 30th" and a price that reverted on the morning of the 30th is an
    # argument with a customer holding a flyer.
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)

    precedence = models.PositiveSmallIntegerField(
        help_text=(
            "Higher wins. Unique within the business, so two lists can never "
            "both claim the same product at the same moment."
        )
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-precedence", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "name"],
                name="unique_price_list_name_per_organization",
            ),
            models.UniqueConstraint(
                fields=["organization", "precedence"],
                name="unique_price_list_precedence_per_organization",
            ),
            # A window that ends before it starts is in force on no day at
            # all. Stored, it looks like a configured promotion that silently
            # never happens, and the shop blames the till.
            models.CheckConstraint(
                condition=(
                    models.Q(starts_on__isnull=True)
                    | models.Q(ends_on__isnull=True)
                    | models.Q(ends_on__gte=models.F("starts_on"))
                ),
                name="price_list_window_is_not_backwards",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def in_force_on(self, day) -> bool:
        """Whether the window covers `day`. Both ends inclusive."""
        if self.starts_on and day < self.starts_on:
            return False
        if self.ends_on and day > self.ends_on:
            return False
        return True


class PriceListBranch(models.Model):
    """
    Where a list applies. No rows at all means everywhere.

    Not exposed as an endpoint of its own — it is written through the price
    list, so there is nothing to scope separately and no way to point one at
    somebody else's branch without the list's own write guard seeing it.
    """

    price_list = models.ForeignKey(
        PriceList, on_delete=models.CASCADE, related_name="branch_links"
    )
    branch = models.ForeignKey(
        "branches.Branches", on_delete=models.CASCADE, related_name="price_lists"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "branch"],
                name="unique_branch_per_price_list",
            )
        ]

    def __str__(self) -> str:
        return f"{self.price_list} @ {self.branch}"


class PriceListEntry(models.Model):
    """
    What one product costs on one list.

    ── A ROW IS THE PRICE; THERE IS NO "UNSET" VALUE ──────────────────────
    The presence of the row is what makes the override apply, which is why
    zero is allowed and means zero. A giveaway is a real thing a shop does,
    and encoding "no override" as 0.00 would make the two indistinguishable
    — the first promotion priced at nothing would be read as a mistake, or a
    mistake would be read as a promotion.

    Negative is refused: a price below nothing pays the customer to take the
    stock, and no shop means that.
    """

    price_list = models.ForeignKey(
        PriceList, on_delete=models.CASCADE, related_name="entries"
    )
    # PROTECT: deleting a product that a live price list names would leave the
    # list quietly pricing nothing. Products are deactivated, not deleted.
    product = models.ForeignKey(
        CatalogCategoryProduct,
        on_delete=models.PROTECT,
        related_name="price_entries",
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["price_list", "product"],
                name="unique_product_per_price_list",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="price_list_entry_is_not_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} = {self.price}"
