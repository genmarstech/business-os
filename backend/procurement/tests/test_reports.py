"""
What the buying reports say, and — more to the point — what they do not.

Two failures are worth more than all the arithmetic here: a report that counts
an order as money spent, and a report that shows one shop another's suppliers.
Both read as perfectly ordinary numbers.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from branches.models import staffAssignment
from identity.authentication import StaffPrincipal
from identity.models import TenantMembership
from inventory.models import BranchInventory

from procurement import reports, services
from procurement.models import PurchaseOrder

from .factories import (
    a_product,
    a_shop,
    a_staff,
    a_subscriber,
    a_supplier,
    assign,
    a_till,
)


def window():
    """Today, as whole local days — the window the API defaults to."""
    today = timezone.localtime().date()
    tz = timezone.get_current_timezone()
    return (
        timezone.make_aware(datetime.combine(today, time.min), tz),
        timezone.make_aware(datetime.combine(today, time.max), tz),
    )


class _Session:
    """The one attribute StaffPrincipal reads. Signing in is not under test."""

    def __init__(self, credential):
        self.credential = credential


class SpendTests(TestCase):
    """
    Ordering is a commitment; receiving is a cost. Every test in this class
    is about keeping those two apart.
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org, name="Brookside")
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )
        self.owner = a_subscriber(self.org, TenantMembership.Role.OWNER, number=901)
        BranchInventory.objects.create(
            branch=self.branch, product=self.milk, quantity=Decimal("0")
        )

    def an_order(self, quantity="10", cost=None):
        order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[
                {
                    "product": self.milk,
                    "quantity": Decimal(quantity),
                    **({"unit_cost": Decimal(cost)} if cost else {}),
                }
            ],
            actor=self.buyer,
        )
        services.submit_order(order)
        return services.approve_order(order, actor=self.owner)

    def test_an_order_nobody_has_delivered_is_not_spend(self):
        """
        The distinction this module exists for. 700 has been committed and
        nothing has cost the shop anything yet; a report that called this
        spend would overstate every quarter with anything in transit.
        """
        self.an_order("10")
        start, end = window()
        figures = reports.overview(self.owner, start, end)

        self.assertEqual(figures["ordered_value"], Decimal("700.00"))
        self.assertEqual(figures["received_value"], Decimal("0.00"))
        self.assertEqual(figures["orders_raised"], 1)
        self.assertEqual(figures["deliveries"], 0)

    def test_a_part_delivery_is_spend_only_for_what_arrived(self):
        order = self.an_order("10")
        services.receive_goods(
            order,
            lines=[{"item": order.items.first(), "quantity": Decimal("4")}],
            actor=self.buyer,
        )
        start, end = window()
        figures = reports.overview(self.owner, start, end)

        self.assertEqual(figures["ordered_value"], Decimal("700.00"))
        self.assertEqual(figures["received_value"], Decimal("280.00"))
        self.assertEqual(figures["deliveries"], 1)

    def test_a_cancelled_order_is_reported_apart_from_the_live_ones(self):
        """
        Netting it off would hide that it happened; leaving it in the ordered
        figure would claim the shop still owes for it.
        """
        self.an_order("10")
        doomed = self.an_order("5")
        services.cancel_order(doomed, reason="Supplier out of stock")

        start, end = window()
        figures = reports.overview(self.owner, start, end)
        self.assertEqual(figures["ordered_value"], Decimal("700.00"))
        self.assertEqual(figures["orders_cancelled"], 1)
        self.assertEqual(figures["cancelled_value"], Decimal("350.00"))

    def test_spend_is_valued_at_the_order_price_not_todays_cost(self):
        """
        The product's cost_price moving must not restate what a delivery cost
        — the same snapshot rule the sale lines follow.
        """
        order = self.an_order("10", cost="70.00")
        services.receive_goods(
            order,
            lines=[{"item": order.items.first(), "quantity": Decimal("10")}],
            actor=self.buyer,
        )
        self.milk.cost_price = Decimal("120.00")
        self.milk.save(update_fields=["cost_price"])

        start, end = window()
        self.assertEqual(
            reports.overview(self.owner, start, end)["received_value"],
            Decimal("700.00"),
        )

    def test_another_shops_buying_is_nowhere_in_the_figures(self):
        other_org, (other_branch,) = a_shop("Shop B")
        other_supplier = a_supplier(other_org, name="Other Dairy")
        other_product = a_product(other_org, name="Other Milk", cost="90.00")
        other_buyer = a_staff(
            other_org, name="B Buyer", email="b@b.co.ke", id_number=2002
        )
        other_owner = a_subscriber(
            other_org, TenantMembership.Role.OWNER, number=902
        )
        BranchInventory.objects.create(
            branch=other_branch, product=other_product, quantity=Decimal("0")
        )
        other = services.raise_order(
            branch=other_branch,
            supplier=other_supplier,
            lines=[{"product": other_product, "quantity": Decimal("100")}],
            actor=other_buyer,
        )
        services.submit_order(other)
        services.approve_order(other, actor=other_owner)
        services.receive_goods(
            other,
            lines=[{"item": other.items.first(), "quantity": Decimal("100")}],
            actor=other_buyer,
        )

        self.an_order("10")
        start, end = window()
        figures = reports.overview(self.owner, start, end)

        self.assertEqual(figures["ordered_value"], Decimal("700.00"))
        self.assertEqual(figures["received_value"], Decimal("0.00"))
        self.assertEqual(
            [row["supplier_name"] for row in reports.by_supplier(self.owner, start, end)],
            ["Brookside"],
        )


class OutstandingTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.supplier = a_supplier(self.org, name="Brookside")
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )
        self.owner = a_subscriber(self.org, TenantMembership.Role.OWNER, number=901)
        BranchInventory.objects.create(
            branch=self.branch, product=self.milk, quantity=Decimal("0")
        )

    def an_order(self, quantity="10", *, expected_at=None, approve=True):
        order = services.raise_order(
            branch=self.branch,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": Decimal(quantity)}],
            actor=self.buyer,
            expected_at=expected_at,
        )
        if approve:
            services.submit_order(order)
            services.approve_order(order, actor=self.owner)
        return order

    def test_a_draft_is_not_committed_money(self):
        """
        Nobody outside the shop has been asked for anything. An abandoned
        shopping list sitting in the obligations figure for ever is the whole
        reason COMMITTED starts at SUBMITTED.
        """
        self.an_order("10", approve=False)
        position = reports.outstanding(self.owner)
        self.assertEqual(position["committed"], Decimal("0.00"))
        self.assertEqual(position["orders"], [])

    def test_what_has_already_arrived_is_no_longer_owed(self):
        order = self.an_order("10")
        services.receive_goods(
            order,
            lines=[{"item": order.items.first(), "quantity": Decimal("4")}],
            actor=self.buyer,
        )
        position = reports.outstanding(self.owner)
        # Six of ten still to come, at 70.
        self.assertEqual(position["committed"], Decimal("420.00"))

    def test_a_finished_order_leaves_the_position_entirely(self):
        order = self.an_order("10")
        services.receive_goods(
            order,
            lines=[{"item": order.items.first(), "quantity": Decimal("10")}],
            actor=self.buyer,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.RECEIVED)
        self.assertEqual(reports.outstanding(self.owner)["committed"], Decimal("0.00"))

    def test_a_late_delivery_is_flagged_and_counted(self):
        yesterday = timezone.localdate() - timedelta(days=3)
        self.an_order("10", expected_at=yesterday)
        position = reports.outstanding(self.owner)

        self.assertEqual(position["overdue"], Decimal("700.00"))
        self.assertTrue(position["orders"][0]["overdue"])
        self.assertEqual(position["orders"][0]["days_late"], 3)

    def test_an_order_with_no_expected_date_is_not_called_late(self):
        """
        Unscheduled is not overdue. Treating a missing date as either is a
        guess, and the guess that calls it late is the one that sends
        somebody to chase a supplier who was never given a deadline.
        """
        self.an_order("10", expected_at=None)
        position = reports.outstanding(self.owner)

        self.assertEqual(position["committed"], Decimal("700.00"))
        self.assertEqual(position["overdue"], Decimal("0.00"))
        self.assertFalse(position["orders"][0]["overdue"])


class ReliabilityTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.quick = a_supplier(self.org, name="Quick Dairy")
        self.slow = a_supplier(self.org, name="Slow Dairy")
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )
        self.owner = a_subscriber(self.org, TenantMembership.Role.OWNER, number=901)
        BranchInventory.objects.create(
            branch=self.branch, product=self.milk, quantity=Decimal("0")
        )

    def delivered(self, supplier, *, expected_at):
        order = services.raise_order(
            branch=self.branch,
            supplier=supplier,
            lines=[{"product": self.milk, "quantity": Decimal("10")}],
            actor=self.buyer,
            expected_at=expected_at,
        )
        services.submit_order(order)
        services.approve_order(order, actor=self.owner)
        services.receive_goods(
            order,
            lines=[{"item": order.items.first(), "quantity": Decimal("10")}],
            actor=self.buyer,
        )
        return order

    def test_on_time_and_late_are_counted_separately(self):
        today = timezone.localdate()
        self.delivered(self.quick, expected_at=today)
        self.delivered(self.slow, expected_at=today - timedelta(days=2))

        start, end = window()
        rows = {
            row["supplier_name"]: row
            for row in reports.supplier_reliability(self.owner, start, end)
        }
        self.assertEqual(rows["Quick Dairy"]["on_time"], 1)
        self.assertEqual(rows["Quick Dairy"]["on_time_rate"], 100)
        self.assertEqual(rows["Slow Dairy"]["late"], 1)
        self.assertEqual(rows["Slow Dairy"]["average_days_late"], 2.0)

    def test_arriving_on_the_promised_day_is_on_time(self):
        """`<=`, not `<`. "By Friday" includes Friday, and a supplier marked
        late for delivering on the day they promised will say so."""
        self.delivered(self.quick, expected_at=timezone.localdate())
        start, end = window()
        row = reports.supplier_reliability(self.owner, start, end)[0]
        self.assertEqual(row["on_time"], 1)
        self.assertEqual(row["late"], 0)

    def test_an_order_with_no_expected_date_is_reported_as_unjudged(self):
        """
        Counted and shown rather than dropped, so a 100% figure computed from
        one of a supplier's forty orders says so on its face.
        """
        self.delivered(self.quick, expected_at=None)
        self.delivered(self.quick, expected_at=timezone.localdate())

        start, end = window()
        row = reports.supplier_reliability(self.owner, start, end)[0]
        self.assertEqual(row["orders"], 2)
        self.assertEqual(row["judged"], 1)
        self.assertEqual(row["unscheduled"], 1)
        self.assertEqual(row["on_time_rate"], 100)

    def test_an_order_still_in_transit_has_no_verdict_yet(self):
        order = services.raise_order(
            branch=self.branch,
            supplier=self.slow,
            lines=[{"product": self.milk, "quantity": Decimal("10")}],
            actor=self.buyer,
            expected_at=timezone.localdate() - timedelta(days=30),
        )
        services.submit_order(order)
        services.approve_order(order, actor=self.owner)

        start, end = window()
        self.assertEqual(reports.supplier_reliability(self.owner, start, end), [])

    def test_a_delivery_booked_in_after_midnight_is_still_counted_late(self):
        """
        ── THE BUG THIS EXISTS TO STOP ────────────────────────────────────
        `received_at` is an aware datetime; `expected_at` is a local date.
        Comparing them without converting compares a UTC date to a Nairobi
        one, and Nairobi is UTC+3 — so the UTC date is the same or EARLIER.

        A delivery booked in at half past midnight is yesterday in UTC. The
        supplier was a day late and the report would call them on time. The
        error runs in the direction that flatters whoever is being measured,
        which is the direction nobody goes looking.

        Set up so it only passes with the conversion: expected yesterday,
        arrived 00:30 today.
        """
        yesterday = timezone.localdate() - timedelta(days=1)
        order = self.delivered(self.quick, expected_at=yesterday)

        tz = timezone.get_current_timezone()
        just_after_midnight = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(0, 30)), tz
        )
        receipt = order.receipts.get()
        receipt.received_at = just_after_midnight
        receipt.save(update_fields=["received_at"])

        start, end = window()
        row = reports.supplier_reliability(self.owner, start, end)[0]
        self.assertEqual(row["late"], 1, "00:30 local is today, not yesterday")
        self.assertEqual(row["on_time"], 0)
        self.assertEqual(row["average_days_late"], 1.0)


class BranchConfinementTests(TestCase):
    """
    `PurchaseOrderViewSet` sets `branch_path`, so a branch manager's order
    LIST stops at their branch. A report over the same rows must stop in the
    same place — otherwise the summary is a way around the scoping of the
    screen it summarises.
    """

    def setUp(self):
        self.org, (self.west, self.karen) = a_shop(
            "Grocers", branches=("Westlands", "Karen")
        )
        self.supplier = a_supplier(self.org, name="Brookside")
        self.milk = a_product(self.org, name="Milk", cost="70.00")
        self.buyer = a_staff(
            self.org, name="Peter Buyer", email="peter@a.co.ke", id_number=1001
        )
        self.owner = a_subscriber(self.org, TenantMembership.Role.OWNER, number=901)

        self.manager = a_staff(
            self.org, name="Grace Manager", email="grace@a.co.ke", id_number=1002
        )
        assign(self.manager, self.west, staffAssignment.StaffRoles.AssistantManager)
        self.principal = StaffPrincipal(_Session(a_till(self.manager, "grace")))

        for branch, quantity in ((self.west, "1"), (self.karen, "8")):
            BranchInventory.objects.create(
                branch=branch, product=self.milk, quantity=Decimal("0")
            )
            order = services.raise_order(
                branch=branch,
                supplier=self.supplier,
                lines=[{"product": self.milk, "quantity": Decimal(quantity)}],
                actor=self.buyer,
            )
            services.submit_order(order)
            services.approve_order(order, actor=self.owner)
            services.receive_goods(
                order,
                lines=[{"item": order.items.first(), "quantity": Decimal(quantity)}],
                actor=self.buyer,
            )

    def test_the_overview_stops_at_their_branch(self):
        start, end = window()
        figures = reports.overview(self.principal, start, end)
        self.assertEqual(figures["received_value"], Decimal("70.00"))
        self.assertEqual(figures["deliveries"], 1)

    def test_the_supplier_table_counts_only_their_branch(self):
        start, end = window()
        rows = reports.by_supplier(self.principal, start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["received_value"], Decimal("70.00"))

    def test_the_owner_still_sees_both_branches(self):
        """`branch_scope` returns None for organisation-wide authority, and
        None means unrestricted — not none."""
        start, end = window()
        self.assertEqual(
            reports.overview(self.owner, start, end)["received_value"],
            Decimal("630.00"),
        )
