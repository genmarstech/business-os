"""
Scanning a shelf: the one field the count screen could not do without.

═══════════════════════════════════════════════════════════════════════════════
THE TILL AND THE STOCK TAKE RESOLVE A SCAN AGAINST DIFFERENT TABLES.

A register holds the catalogue, so it has had `barcode` all along. A stock take
holds BranchInventory rows — one per product per branch — and those carried
`product_name` and `product_sku` and nothing a scanner emits. So the only way
through an aisle was typing product names on a phone, which is the job the
scanner exists to remove.

`product_barcode` is read through the join rather than copied onto the
inventory row. One product on four branches' shelves is the same digits, and a
duplicated column would be four places to go wrong the day a code is corrected.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches
from catalog.models import CatalogCategories, CatalogCategoryProduct
from django.test import TestCase

from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization


def a_shop(name):
    org = BusinessOrganization.objects.create(name=name)
    branch = Branches.objects.create(
        organization=org,
        branch_name=f"{name} Main",
        branch_location="Nairobi",
        branch_allocation="Ground floor",
        branch_manager="A Manager",
        is_active=True,
    )
    return org, branch


def a_product(org, *, name, sku, barcode=""):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
    return CatalogCategoryProduct.objects.create(
        organization=org,
        category=category,
        name=name,
        sku=sku,
        barcode=barcode,
        cost_price=Decimal("70.00"),
        selling_price=Decimal("100.00"),
    )


class InventoryCarriesTheBarcodeTests(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Kilimani Dental")

        self.owner = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke", full_name="Asha Owner"
        )
        TenantMembership.objects.create(
            account=self.owner,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()

        self.scannable = BranchInventory.objects.create(
            branch=self.branch,
            product=a_product(
                self.org, name="Blue Band 500g", sku="BB500", barcode="6001234567890"
            ),
            quantity=Decimal("12"),
        )
        self.loose = BranchInventory.objects.create(
            branch=self.branch,
            product=a_product(self.org, name="Loose sugar", sku="SUG-KG"),
            quantity=Decimal("40"),
        )

    def rows(self):
        response = self.client.get("/invt/inventory/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        return body["results"] if isinstance(body, dict) else body

    def test_the_barcode_is_on_the_row_a_stock_take_reads(self):
        found = {r["id"]: r for r in self.rows()}
        self.assertEqual(
            found[self.scannable.pk]["product_barcode"], "6001234567890"
        )

    def test_a_product_with_no_barcode_reports_an_empty_string(self):
        """
        Not absent, and not null. A screen filters on it, and a field that is
        sometimes missing is a screen that has to handle three cases where
        there are two — loose goods and anything sold by weight genuinely have
        no barcode, which is ordinary rather than exceptional.
        """
        found = {r["id"]: r for r in self.rows()}
        self.assertEqual(found[self.loose.pk]["product_barcode"], "")

    def test_the_barcode_cannot_be_written_through_the_inventory_row(self):
        """
        ⚠ It belongs to the product, and there is one product behind every
          branch's shelf row. A writable copy here would let a correction at
          one branch disagree with the same product at another, which is the
          duplicate the join exists to avoid.
        """
        response = self.client.patch(
            f"/invt/inventory/{self.scannable.pk}/",
            {"product_barcode": "9999999999999"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        self.scannable.product.refresh_from_db()
        self.assertEqual(self.scannable.product.barcode, "6001234567890")

    def test_another_shops_barcodes_are_not_in_the_answer(self):
        """
        The field is new; the isolation it has to survive is not. A barcode is
        the same digits in every shop that stocks the product, so a leak here
        would be a way to read another tenant's shelf by scanning something
        off your own.
        """
        other, other_branch = a_shop("Someone Else")
        BranchInventory.objects.create(
            branch=other_branch,
            product=a_product(
                other, name="Their Milk", sku="MILK", barcode="6009999999999"
            ),
            quantity=Decimal("5"),
        )

        codes = {r["product_barcode"] for r in self.rows()}
        self.assertIn("6001234567890", codes)
        self.assertNotIn("6009999999999", codes)

    def test_listing_shelves_does_not_query_per_row(self):
        """
        `product_barcode` is a second field read through `product`, so a
        dropped `select_related` would turn one query into one per shelf row —
        and a stock take lists every product in a branch at once, which is the
        screen where that is most expensive and least visible.

        ── IT ASSERTS THE SHAPE, NOT A NUMBER ──────────────────────────────
        A literal query count is a test that fails whenever somebody adds a
        cache lookup or a middleware read, which teaches people to edit the
        number until it passes. What matters is that the count does not GROW
        with the rows, so it is measured twice and compared.
        """

        def queries() -> int:
            from django.test.utils import CaptureQueriesContext
            from django.db import connection

            with CaptureQueriesContext(connection) as captured:
                response = self.client.get("/invt/inventory/")
                self.assertEqual(response.status_code, 200, response.content)
            return len(captured)

        few = queries()

        for n in range(20):
            BranchInventory.objects.create(
                branch=self.branch,
                product=a_product(
                    self.org,
                    name=f"Product {n}",
                    sku=f"SKU{n}",
                    barcode=f"60012345{n:05d}",
                ),
                quantity=Decimal("3"),
            )

        self.assertEqual(
            queries(),
            few,
            "Listing shelves queried more often with more rows — the join on "
            "`product` has been dropped and every row is fetching its own "
            "barcode.",
        )
