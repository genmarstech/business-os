"""
Drafting an order the moment a shelf crosses its reorder level, unattended.

══════════════════════════════════════════════════════════════════════════════
TWO THINGS HAVE TO BE SET UP, AND MOST OF THESE TESTS ARE ABOUT WHAT HAPPENS
WHEN ONE OF THEM IS MISSING.

A preferred supplier and a reorder quantity are both opt-in, defaulting to
"do nothing" the same way `reorder_level` itself does — see the banner on
`CatalogCategoryProduct.preferred_supplier`. A shop that has not configured
either must see no automatic order, never a guessed one.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from inventory import services as inventory_services
from inventory.models import BranchInventory
from procurement.models import PurchaseOrder, PurchaseOrderItem

from .factories import a_product, a_shop, a_supplier


class AutoReplenishTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org)
        self.product = a_product(self.org, name="Soda", cost="40.00")
        self.inventory = BranchInventory.objects.create(
            branch=self.branch,
            product=self.product,
            quantity=Decimal("20"),
            reorder_level=Decimal("10"),
            reorder_quantity=Decimal("24"),
        )

    def cross_the_line(self, *, start="20", drop_to="5"):
        """Count the shelf down from `start` to `drop_to` in one movement."""
        self.inventory.quantity = Decimal(start)
        self.inventory.save(update_fields=["quantity"])
        delta = Decimal(drop_to) - Decimal(start)
        return inventory_services.adjust(
            inventory=self.inventory, delta=delta, reason="OTHER"
        )

    # ── the ordinary case ────────────────────────────────────────────────

    def test_crossing_with_both_things_configured_drafts_an_order(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.cross_the_line()

        order = PurchaseOrder.objects.get(branch=self.branch)
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.supplier_id, self.supplier.pk)
        item = order.items.get()
        self.assertEqual(item.product_id, self.product.pk)
        # units_per_pack defaults to 1, so 24 sellable units is 24 packs.
        self.assertEqual(item.quantity_ordered, Decimal("24.00"))
        self.assertIn("fell to", order.note)

    def test_the_order_quantity_is_converted_to_whole_packs(self):
        self.product.preferred_supplier = self.supplier
        self.product.units_per_pack = Decimal("24")
        self.product.save(update_fields=["preferred_supplier", "units_per_pack"])
        # 50 sellable units at 24 a pack is 2.08 packs — rounds UP, because a
        # supplier cannot deliver four-fifths of a carton.
        self.inventory.reorder_quantity = Decimal("50")
        self.inventory.save(update_fields=["reorder_quantity"])

        self.cross_the_line()

        item = PurchaseOrderItem.objects.get(product=self.product)
        self.assertEqual(item.quantity_ordered, Decimal("3"))

    # ── either thing missing means nothing happens ──────────────────────

    def test_no_preferred_supplier_means_no_order(self):
        self.cross_the_line()
        self.assertFalse(PurchaseOrder.objects.filter(branch=self.branch).exists())

    def test_zero_reorder_quantity_means_no_order(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])
        self.inventory.reorder_quantity = Decimal("0")
        self.inventory.save(update_fields=["reorder_quantity"])

        self.cross_the_line()

        self.assertFalse(PurchaseOrder.objects.filter(branch=self.branch).exists())

    def test_an_archived_supplier_is_not_ordered_from(self):
        self.supplier.is_active = False
        self.supplier.save(update_fields=["is_active"])
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.cross_the_line()

        self.assertFalse(PurchaseOrder.objects.filter(branch=self.branch).exists())

    # ── the crossing, not the state ──────────────────────────────────────

    def test_a_sale_that_stays_below_the_line_does_not_draft_a_second_order(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.cross_the_line()
        self.assertEqual(PurchaseOrder.objects.filter(branch=self.branch).count(), 1)

        # Already below the line before this move — was_fine is false, so
        # this must not raise a second draft while the first is still open.
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("-1"), reason="OTHER"
        )
        self.assertEqual(PurchaseOrder.objects.filter(branch=self.branch).count(), 1)

    def test_a_delivery_crossing_upward_drafts_nothing(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.inventory.quantity = Decimal("2")
        self.inventory.save(update_fields=["quantity"])
        inventory_services.adjust(
            inventory=self.inventory, delta=Decimal("50"), reason="DELIVERY"
        )

        self.assertFalse(PurchaseOrder.objects.filter(branch=self.branch).exists())

    def test_an_order_still_open_is_not_duplicated(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.cross_the_line()
        first = PurchaseOrder.objects.get(branch=self.branch)

        # Stock recovers above the line, then crosses it again while the
        # first draft is still sitting there, unsubmitted.
        self.inventory.quantity = Decimal("20")
        self.inventory.save(update_fields=["quantity"])
        self.cross_the_line()

        self.assertEqual(
            list(PurchaseOrder.objects.filter(branch=self.branch)), [first]
        )

    def test_once_the_open_order_is_cancelled_a_new_crossing_drafts_again(self):
        from procurement import services as procurement_services

        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        self.cross_the_line()
        first = PurchaseOrder.objects.get(branch=self.branch)
        procurement_services.cancel_order(first, reason="Wrong supplier")

        self.inventory.quantity = Decimal("20")
        self.inventory.save(update_fields=["quantity"])
        self.cross_the_line()

        self.assertEqual(PurchaseOrder.objects.filter(branch=self.branch).count(), 2)

    # ── it cannot fail the thing that triggered it ──────────────────────

    def test_a_broken_draft_does_not_break_the_stock_movement_that_triggered_it(self):
        self.product.preferred_supplier = self.supplier
        self.product.save(update_fields=["preferred_supplier"])

        with patch(
            "procurement.services.raise_order", side_effect=RuntimeError("boom")
        ):
            adjustment = self.cross_the_line()

        self.assertIsNotNone(adjustment)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.quantity, Decimal("5.00"))
        self.assertFalse(PurchaseOrder.objects.filter(branch=self.branch).exists())
