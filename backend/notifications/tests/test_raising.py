"""
That notifications are actually raised — which `_safely` makes possible to get
wrong without anything failing.

═══════════════════════════════════════════════════════════════════════════════
THIS FILE EXISTS BECAUSE THE WHOLE FEATURE FAILS SILENTLY BY DESIGN.

`services._safely` swallows every exception a raise throws, and it has to: these
are called inside the transactions that take payments, close drawers and book
deliveries, and a notification must never be able to fail a sale.

The cost is that a broken notification looks exactly like a working one. The
existing 615 tests all passed the moment the hooks were added, which proves only
that nothing BROKE — not that anything was written. So every test here asserts a
row exists, and the three stock tests go through the three real service
functions rather than calling the raise directly.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from django.test import TestCase

from identity import services as identity_services
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory import services as inventory_services
from inventory.models import BranchInventory
from notifications.models import Notification
from organisations.models import BusinessOrganization, OrganizationStaff


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


def a_person(org, name, n):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=f"p{n}@example.co.ke",
        phone_number=f"+2547{n:08d}",
        address="Nairobi",
        id_number=n,
    )


def a_product(org, *, name, sku, price="100.00"):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
    return CatalogCategoryProduct.objects.create(
        organization=org,
        category=category,
        name=name,
        sku=sku,
        cost_price=Decimal("70.00"),
        selling_price=Decimal(price),
    )


def shelf(branch, product, *, quantity, reorder="5"):
    return BranchInventory.objects.create(
        branch=branch,
        product=product,
        quantity=Decimal(quantity),
        reorder_level=Decimal(reorder),
    )


class StockCrossingBase(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Kilimani Dental")
        self.product = a_product(self.org, name="Blue Band 500g", sku="BB500")
        self.inventory = shelf(self.branch, self.product, quantity="20", reorder="5")

    def kinds(self):
        return sorted(
            Notification.objects.filter(resolved_at__isnull=True).values_list(
                "kind", flat=True
            )
        )


class StockIsWrittenInThreePlacesTests(StockCrossingBase):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE GUARD AGAINST A FOURTH WRITER.

    `BranchInventory.quantity` is written in three service modules and there is
    no choke point. The most important is the SALE — stock runs low because of
    selling, so a low-stock notification wired only to the manual adjustment
    screen would be silent for the shop actually running out.

    A fourth writer added without a fourth call will leave one of these
    passing and the shop quietly un-notified, so each path is exercised
    through its real service function.
    ══════════════════════════════════════════════════════════════════════════
    """

    def test_an_adjustment_raises_when_it_crosses_the_level(self):
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-16"), reason="COUNT"
        )
        self.assertEqual(self.kinds(), ["stock.low"])

    def test_a_sale_raises_when_it_crosses_the_level(self):
        """
        ⚠ THE ONE THAT MATTERS. A shop does not run out because somebody
          corrected a figure; it runs out because it sold things.
        """
        from sales import services as sales_services

        person = a_person(self.org, "Jane Cashier", 10000001)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment="CA"
        )
        register = Register.objects.create(
            branch=self.branch, name="Till 1", is_active=True
        )
        shift = RegisterShift.objects.create(
            register=register, operator=person, opening_cash=Decimal("0"),
            status="OPEN",
        )

        sales_services.checkout(
            shift=shift,
            cashier=person,
            lines=[
                {
                    "product": self.product,
                    "quantity": Decimal("16"),
                    "discount": Decimal("0"),
                }
            ],
            payments=[{"method": "CASH", "amount": Decimal("1600.00")}],
        )

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("4"))
        self.assertEqual(self.kinds(), ["stock.low"])

    def test_a_delivery_resolves_what_a_sale_raised(self):
        """The other direction, through procurement's own receiving path."""
        from procurement import services as procurement_services
        from procurement.models import Supplier

        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-18"), reason="COUNT"
        )
        self.assertEqual(self.kinds(), ["stock.low"])

        supplier = Supplier.objects.create(
            organization=self.org, name="A Supplier", is_active=True
        )
        order = procurement_services.raise_order(
            branch=self.branch,
            supplier=supplier,
            lines=[
                {
                    "product": self.product,
                    "quantity": Decimal("30"),
                    "unit_cost": Decimal("70.00"),
                }
            ],
        )
        # An approval has to be recorded against a person — the second
        # signature is the whole point of the permission split.
        approver = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke", full_name="Asha Owner"
        )
        TenantMembership.objects.create(
            account=approver,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        procurement_services.submit_order(order)
        procurement_services.approve_order(order, actor=approver)
        procurement_services.receive_goods(
            order=order,
            lines=[{"item": order.items.get(), "quantity": Decimal("30")}],
        )

        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("32"))
        # The low-stock row is resolved, so it is gone from the feed.
        self.assertNotIn("stock.low", self.kinds())


class StockCrossingBehaviourTests(StockCrossingBase):
    def test_nothing_is_raised_while_the_shelf_is_healthy(self):
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-1"), reason="COUNT"
        )
        self.assertEqual(Notification.objects.count(), 0)

    def test_out_of_stock_supersedes_low(self):
        """
        Two rows for one product means a shop reads the quieter one. Falling to
        zero resolves the low row and raises the urgent one.
        """
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-16"), reason="COUNT"
        )
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-4"), reason="COUNT"
        )
        self.assertEqual(self.kinds(), ["stock.out"])

    def test_a_second_sale_does_not_raise_a_second_notification(self):
        """
        ⚠ WITHOUT DEDUPE THIS IS A NOTIFICATION PER SALE of a popular item,
          which is how a feed becomes something people stop reading.
        """
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-16"), reason="COUNT"
        )
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-1"), reason="COUNT"
        )
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-1"), reason="COUNT"
        )
        self.assertEqual(
            Notification.objects.filter(
                kind="stock.low", resolved_at__isnull=True
            ).count(),
            1,
        )

    def test_restocking_resolves_rather_than_deletes(self):
        """
        "How long were we out of sugar last month" is a question somebody asks,
        so a resolved row leaves the feed and stays in the table.
        """
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-20"), reason="COUNT"
        )
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("30"), reason="COUNT"
        )

        self.assertEqual(self.kinds(), [])
        self.assertTrue(
            Notification.objects.filter(
                kind="stock.out", resolved_at__isnull=False
            ).exists()
        )

    def test_a_shelf_with_no_reorder_level_is_never_low(self):
        """
        `reorder_level` of zero means "tell me when it is gone", not "tell me
        now". Out of stock still fires; low does not.
        """
        quiet = shelf(
            self.branch,
            a_product(self.org, name="Loose sugar", sku="SUG"),
            quantity="10",
            reorder="0",
        )
        inventory_services.adjust(inventory=quiet, delta=Decimal("-9"), reason="COUNT")
        self.assertEqual(Notification.objects.count(), 0)

        inventory_services.adjust(inventory=quiet, delta=Decimal("-1"), reason="COUNT")
        self.assertEqual(self.kinds(), ["stock.out"])
