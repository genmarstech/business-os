"""
Raising, sending, approving and cancelling an order.

The tests that matter here are the ones where a failure costs the shop money
or loses the trail: an order edited after a supplier was sent it, an approval
attributed to nobody, a line copied by reference instead of by value.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from identity.models import TenantMembership

from procurement import services
from procurement.models import PurchaseOrder, PurchaseOrderItem

from .factories import a_product, a_shop, a_staff, a_subscriber, a_supplier


class RaisingTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org)
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.bread = a_product(self.org, name="Bread", cost="55.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )

    def raise_one(self, **kwargs):
        return services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=kwargs.pop(
                "lines",
                [{"product": self.milk, "quantity": Decimal("10")}],
            ),
            actor=kwargs.pop("actor", self.buyer),
            **kwargs,
        )

    def test_an_order_is_written_with_its_lines_and_a_total(self):
        order = self.raise_one(
            lines=[
                {"product": self.milk, "quantity": Decimal("10")},
                {"product": self.bread, "quantity": Decimal("4"), "unit_cost": "50.00"},
            ]
        )
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(order.items.count(), 2)
        # 10 × 70 + 4 × 50
        self.assertEqual(order.total, Decimal("900.00"))

    def test_the_first_order_starts_the_shops_own_sequence(self):
        """
        Per organisation, like every other number here. A platform-wide
        sequence would let a tenant measure how much everybody else is
        buying.
        """
        first = self.raise_one()
        second = self.raise_one()
        self.assertEqual(first.number, services.FIRST_ORDER_NUMBER)
        self.assertEqual(second.number, services.FIRST_ORDER_NUMBER + 1)

        other_org, (other_branch,) = a_shop("Shop B")
        other = services.raise_order(
            branch=other_branch,
            supplier=a_supplier(other_org, name="Other"),
            lines=[{"product": a_product(other_org), "quantity": Decimal("1")}],
        )
        self.assertEqual(other.number, services.FIRST_ORDER_NUMBER)

    def test_the_cost_on_a_line_is_a_copy(self):
        """
        Raising a supplier's price next month must not rewrite what this
        order committed to — the same rule SaleItem follows for selling
        prices.
        """
        order = self.raise_one()
        self.milk.cost_price = Decimal("95.00")
        self.milk.name = "Fresh Milk 500ml"
        self.milk.save()

        line = order.items.get()
        self.assertEqual(line.unit_cost, Decimal("70.00"))
        self.assertEqual(line.product_name, "Milk")
        self.assertEqual(line.line_total, Decimal("700.00"))

    def test_a_cost_may_be_stated_rather_than_taken_from_the_catalogue(self):
        order = self.raise_one(
            lines=[{"product": self.milk, "quantity": "6", "unit_cost": "64.50"}]
        )
        self.assertEqual(order.items.get().unit_cost, Decimal("64.50"))
        self.assertEqual(order.total, Decimal("387.00"))

    def test_another_shops_product_cannot_be_ordered(self):
        other_org, _ = a_shop("Shop B")
        theirs = a_product(other_org, name="Sugar")
        with self.assertRaises(ValidationError):
            self.raise_one(lines=[{"product": theirs, "quantity": "1"}])
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_another_shops_supplier_cannot_be_ordered_from(self):
        other_org, _ = a_shop("Shop B")
        with self.assertRaises(ValidationError):
            services.raise_order(
                branch=self.branch,
                supplier=a_supplier(other_org, name="Theirs"),
                lines=[{"product": self.milk, "quantity": "1"}],
            )
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_one_product_cannot_be_on_an_order_twice(self):
        """
        Two lines for the same product would receive twice against one
        outstanding figure, and the arithmetic downstream stops meaning
        anything.
        """
        with self.assertRaises(ValidationError):
            self.raise_one(
                lines=[
                    {"product": self.milk, "quantity": "1"},
                    {"product": self.milk, "quantity": "2"},
                ]
            )

    def test_an_empty_order_is_refused(self):
        with self.assertRaises(ValidationError):
            self.raise_one(lines=[])

    def test_a_quantity_of_none_is_refused(self):
        for bad in ("0", "-3", "", "abc", None):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValidationError):
                    self.raise_one(
                        lines=[{"product": self.milk, "quantity": bad}]
                    )

    def test_an_archived_supplier_cannot_be_ordered_from(self):
        self.supplier.is_active = False
        self.supplier.save()
        with self.assertRaises(ValidationError):
            self.raise_one()

    def test_a_retry_with_the_same_key_returns_the_first_order(self):
        """
        §11. A double-tapped "Raise order" on a slow connection is two
        lorries, and the second one is somebody's money.
        """
        first = self.raise_one(idempotency_key="abc-123")
        again = self.raise_one(idempotency_key="abc-123")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

    def test_nothing_is_written_when_a_line_is_refused(self):
        """
        The whole order or none of it. A header with no lines is an order
        nobody can act on and a number burned out of the sequence.
        """
        other_org, _ = a_shop("Shop B")
        with self.assertRaises(ValidationError):
            self.raise_one(
                lines=[
                    {"product": self.milk, "quantity": "5"},
                    {"product": a_product(other_org, name="Sugar"), "quantity": "5"},
                ]
            )
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        self.assertEqual(PurchaseOrderItem.objects.count(), 0)

    def test_an_order_records_who_raised_it(self):
        order = self.raise_one()
        self.assertEqual(order.raised_by_staff, self.buyer)
        self.assertIsNone(order.raised_by_account_id)
        self.assertEqual(order.raised_by_name, "Peter Buyer")

    def test_an_owner_raising_one_is_recorded_as_themselves(self):
        owner = a_subscriber(self.org, TenantMembership.Role.OWNER, number=7)
        order = self.raise_one(actor=owner)
        self.assertEqual(order.raised_by_account, owner)
        self.assertIsNone(order.raised_by_staff_id)


class EditingTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org)
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.bread = a_product(self.org, name="Bread", cost="55.00")
        self.order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "10"}],
        )

    def test_a_draft_may_be_rewritten(self):
        order = services.replace_lines(
            self.order,
            lines=[{"product": self.bread, "quantity": "4", "unit_cost": "50.00"}],
        )
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().product_name, "Bread")
        self.assertEqual(order.total, Decimal("200.00"))

    def test_a_sent_order_may_not_be_rewritten(self):
        """
        The paperwork in the supplier's hand and the paperwork here would
        disagree, and the delivery that arrives would match neither.
        """
        services.submit_order(self.order)
        self.order.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.replace_lines(
                self.order, lines=[{"product": self.bread, "quantity": "4"}]
            )
        self.assertEqual(self.order.items.get().product_name, "Milk")


class ApprovalTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org)
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )
        self.manager = a_staff(
            self.org, name="Mary Manager", email="mary@a.co.ke", id_number=1002
        )
        self.order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "10"}],
            actor=self.buyer,
        )

    def test_a_draft_cannot_be_approved_before_it_is_sent(self):
        with self.assertRaises(ValidationError):
            services.approve_order(self.order, actor=self.manager)

    def test_sending_then_approving_records_who_and_when(self):
        services.submit_order(self.order)
        self.order.refresh_from_db()
        approved = services.approve_order(self.order, actor=self.manager)

        self.assertEqual(approved.status, PurchaseOrder.Status.APPROVED)
        self.assertEqual(approved.approved_by_staff, self.manager)
        self.assertIsNotNone(approved.approved_at)
        self.assertEqual(approved.approved_by_name, "Mary Manager")

    def test_an_approval_by_nobody_is_refused(self):
        """
        Unlike raising an order, which tolerates an unattributable actor.
        This row exists to answer "who agreed to spend this", and a NULL in
        both columns answers nobody.
        """
        services.submit_order(self.order)
        self.order.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.approve_order(self.order, actor=None)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.Status.SUBMITTED)

    def test_an_order_cannot_be_approved_twice(self):
        services.submit_order(self.order)
        self.order.refresh_from_db()
        services.approve_order(self.order, actor=self.manager)
        self.order.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.approve_order(self.order, actor=self.buyer)

    def test_a_cancelled_order_stays_cancelled(self):
        services.cancel_order(self.order, reason="Supplier out of stock")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, PurchaseOrder.Status.CANCELLED)
        self.assertEqual(self.order.cancelled_reason, "Supplier out of stock")

        for call in (
            lambda: services.submit_order(self.order),
            lambda: services.cancel_order(self.order),
        ):
            with self.assertRaises(ValidationError):
                call()

    def test_an_expected_date_is_kept_as_given(self):
        order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "1"}],
            expected_at=date(2026, 11, 3),
        )
        self.assertEqual(order.expected_at, date(2026, 11, 3))
