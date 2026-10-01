"""
Booking in a delivery.

This is where procurement touches the one figure in a shop that cannot be
reconstructed from anything else. Every test here is one where failure means
stock the system believes in and the shelf does not — found weeks later by a
stock take, and usually blamed on theft.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import BranchInventory, StockAdjustment, StockMovement
from procurement import services
from procurement.models import GoodsReceipt, PurchaseOrder

from .factories import a_product, a_shop, a_staff, a_supplier


class ReceivingTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org)
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.bread = a_product(self.org, name="Bread", cost="55.00")
        self.storeman = a_staff(
            self.org, name="Sam Store", email="sam@a.co.ke", id_number=1003
        )
        self.order = self.an_approved_order()

    def an_approved_order(self, lines=None):
        order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=lines
            or [
                {"product": self.milk, "quantity": "10"},
                {"product": self.bread, "quantity": "4"},
            ],
        )
        services.submit_order(order)
        order.refresh_from_db()
        services.approve_order(order, actor=self.storeman)
        order.refresh_from_db()
        return order

    def lines(self, **quantities):
        """`milk="10"` → the line for milk, that many."""
        by_name = {item.product_name.lower(): item for item in self.order.items.all()}
        return [
            {"item": by_name[name], "quantity": Decimal(quantity)}
            for name, quantity in quantities.items()
        ]

    def test_a_full_delivery_closes_the_order(self):
        receipt = services.receive_goods(
            self.order,
            lines=self.lines(milk="10", bread="4"),
            actor=self.storeman,
            delivery_note="DN-5512",
        )
        self.order.refresh_from_db()

        self.assertEqual(receipt.number, services.FIRST_RECEIPT_NUMBER)
        self.assertEqual(receipt.items.count(), 2)
        self.assertEqual(receipt.delivery_note, "DN-5512")
        self.assertEqual(self.order.status, PurchaseOrder.Status.RECEIVED)

    def test_stock_goes_up_and_says_why(self):
        """
        Blueprint §10. A quantity that moved with no movement behind it is
        what makes a stock take impossible to reconcile — and before this app
        existed, the only way stock went up was somebody typing a number.
        """
        receipt = services.receive_goods(
            self.order, lines=self.lines(milk="10"), actor=self.storeman
        )

        inventory = BranchInventory.objects.get(branch=self.branch, product=self.milk)
        self.assertEqual(inventory.quantity, Decimal("10.00"))

        movement = StockMovement.objects.get(inventory=inventory)
        self.assertEqual(movement.movement_type, "PURCHASE")
        self.assertEqual(movement.quantity_before, Decimal("0.00"))
        self.assertEqual(movement.quantity_after, Decimal("10.00"))
        self.assertIn(str(receipt.number), movement.reference)
        self.assertIn(str(self.order.number), movement.reference)

    def test_a_delivery_does_not_also_book_an_adjustment(self):
        """
        inventory/services.adjust writes a StockAdjustment because a manual
        correction has no other document to point at. A delivery has one, and
        booking both would double every delivery in any report that counts
        adjustments.
        """
        services.receive_goods(
            self.order, lines=self.lines(milk="10"), actor=self.storeman
        )
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_a_first_delivery_of_a_new_line_creates_the_shelf_row(self):
        """
        Refusing because nobody had pre-created an empty inventory row would
        send a buyer to another screen with a driver waiting.
        """
        self.assertFalse(
            BranchInventory.objects.filter(
                branch=self.branch, product=self.bread
            ).exists()
        )
        services.receive_goods(
            self.order, lines=self.lines(bread="4"), actor=self.storeman
        )
        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.branch, product=self.bread
            ).quantity,
            Decimal("4.00"),
        )

    def test_a_part_delivery_leaves_the_order_open(self):
        services.receive_goods(
            self.order, lines=self.lines(milk="6"), actor=self.storeman
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.Status.PART_RECEIVED)

        line = self.order.items.get(product=self.milk)
        self.assertEqual(line.quantity_received, Decimal("6.00"))
        self.assertEqual(line.outstanding, Decimal("4.00"))

    def test_the_rest_of_it_closes_the_order(self):
        services.receive_goods(
            self.order, lines=self.lines(milk="6"), actor=self.storeman
        )
        self.order.refresh_from_db()
        services.receive_goods(
            self.order,
            lines=self.lines(milk="4", bread="4"),
            actor=self.storeman,
        )
        self.order.refresh_from_db()

        self.assertEqual(self.order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(GoodsReceipt.objects.count(), 2)
        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.branch, product=self.milk
            ).quantity,
            Decimal("10.00"),
        )

    def test_more_than_was_ordered_is_refused_and_moves_nothing(self):
        """
        Over-receipt is the quiet one. Twelve arriving against an order for
        ten is either a supplier error or a cost nobody approved, and
        accepting it silently makes the order and the invoice disagree with
        no document explaining the two extra.
        """
        with self.assertRaises(ValidationError):
            services.receive_goods(
                self.order, lines=self.lines(milk="12"), actor=self.storeman
            )

        self.assertEqual(GoodsReceipt.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertFalse(
            BranchInventory.objects.filter(
                branch=self.branch, product=self.milk
            ).exists()
        )

    def test_an_unapproved_order_receives_nothing(self):
        draft = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "5"}],
        )
        with self.assertRaises(ValidationError):
            services.receive_goods(
                draft,
                lines=[{"item": draft.items.get(), "quantity": Decimal("5")}],
                actor=self.storeman,
            )
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_a_cancelled_order_receives_nothing(self):
        services.cancel_order(self.order, reason="Supplier closed")
        self.order.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.receive_goods(
                self.order, lines=self.lines(milk="1"), actor=self.storeman
            )

    def test_a_line_from_another_order_is_refused(self):
        other = self.an_approved_order(
            lines=[{"product": self.milk, "quantity": "3"}]
        )
        with self.assertRaises(ValidationError):
            services.receive_goods(
                self.order,
                lines=[{"item": other.items.get(), "quantity": Decimal("1")}],
                actor=self.storeman,
            )

    def test_the_same_line_twice_in_one_delivery_is_refused(self):
        line = self.order.items.get(product=self.milk)
        with self.assertRaises(ValidationError):
            services.receive_goods(
                self.order,
                lines=[
                    {"item": line, "quantity": Decimal("5")},
                    {"item": line, "quantity": Decimal("5")},
                ],
                actor=self.storeman,
            )
        self.assertEqual(GoodsReceipt.objects.count(), 0)

    def test_a_retry_with_the_same_key_books_one_delivery(self):
        """
        A delivery counted twice is stock the shop thinks it has and does
        not. The retry has to come back with the original receipt.
        """
        first = services.receive_goods(
            self.order,
            lines=self.lines(milk="10"),
            actor=self.storeman,
            idempotency_key="dn-5512",
        )
        self.order.refresh_from_db()
        again = services.receive_goods(
            self.order,
            lines=self.lines(milk="10"),
            actor=self.storeman,
            idempotency_key="dn-5512",
        )

        self.assertEqual(first.pk, again.pk)
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.branch, product=self.milk
            ).quantity,
            Decimal("10.00"),
        )

    def test_a_line_read_before_the_first_delivery_cannot_over_receive(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE STALE-LINE RACE, MADE DETERMINISTIC.

        A serialiser resolves the line BEFORE the order is locked, so a
        second delivery booked at the same moment as the first holds an
        object that still says nothing has been received. If the service
        measured against that object, six and six would both pass against an
        outstanding ten.

        Holding the object across two calls is the same staleness without the
        threads.
        ══════════════════════════════════════════════════════════════════
        """
        stale = self.order.items.get(product=self.milk)
        services.receive_goods(
            self.order, lines=[{"item": stale, "quantity": Decimal("6")}],
            actor=self.storeman,
        )
        self.order.refresh_from_db()
        self.assertEqual(stale.quantity_received, Decimal("0.00"))

        with self.assertRaises(ValidationError):
            services.receive_goods(
                self.order, lines=[{"item": stale, "quantity": Decimal("6")}],
                actor=self.storeman,
            )

        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.branch, product=self.milk
            ).quantity,
            Decimal("6.00"),
        )

    def test_a_delivery_records_who_counted_it(self):
        receipt = services.receive_goods(
            self.order, lines=self.lines(milk="1"), actor=self.storeman
        )
        self.assertEqual(receipt.received_by_staff, self.storeman)
        self.assertEqual(receipt.received_by_name, "Sam Store")

    def test_the_receipt_line_keeps_the_cost_it_was_ordered_at(self):
        receipt = services.receive_goods(
            self.order, lines=self.lines(milk="10"), actor=self.storeman
        )
        self.assertEqual(receipt.items.get().unit_cost, Decimal("70.00"))

    def test_receiving_does_not_restate_the_catalogue_cost(self):
        """
        Deliberate, and the one line of code somebody will eventually add.
        cost_price is what every margin report measures against, so a single
        delivery at a promotional price would silently restate the
        profitability of everything sold before it.
        """
        services.receive_goods(
            self.order, lines=self.lines(milk="10"), actor=self.storeman
        )
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.cost_price, Decimal("70.00"))
