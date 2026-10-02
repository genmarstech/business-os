"""
Counting the shelves, and booking what the count found.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS: test_a_sale_during_the_count_is_not_a_discrepancy.

A shop keeps trading while somebody counts it. If the expected figure were
read at close rather than at the moment of counting, every sale made during
the count would land in the variance, and a stock take would report the
afternoon's trade as missing stock. That is the error that makes a shop
distrust its own count and stop doing them.

Second: test_closing_moves_stock_through_a_movement. The whole inventory app
is built on "a quantity never moves without a row saying why", and a stock
take is the operation most tempted to break it — it already knows the number
it wants the quantity to be.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from branches.models import Branches
from catalog.models import CatalogCategories, CatalogCategoryProduct
from inventory import services
from inventory.models import (
    BranchInventory,
    StockCount,
    StockCountLine,
    StockMovement,
)
from organisations.models import BusinessOrganization, OrganizationStaff


def a_shop(name="Shop A"):
    org = BusinessOrganization.objects.create(name=name)
    branch = Branches.objects.create(
        organization=org,
        branch_name="Westlands",
        branch_location="Nairobi",
        branch_allocation="Ground floor",
        branch_manager="A Manager",
        is_active=True,
    )
    category = CatalogCategories.objects.create(organization=org, name="General")
    return org, branch, category


def a_product(org, category, name, sku, quantity, branch):
    product = CatalogCategoryProduct.objects.create(
        organization=org,
        category=category,
        name=name,
        sku=sku,
        cost_price=Decimal("70.00"),
        selling_price=Decimal("100.00"),
    )
    return BranchInventory.objects.create(
        branch=branch, product=product, quantity=Decimal(quantity)
    )


def a_person(org, name, n):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=f"{name.split()[0].lower()}{n}@example.co.ke",
        phone_number=f"+2547000000{n:02d}",
        address="Nairobi",
        id_number=n,
    )


class StockCountTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.category = a_shop()
        self.milk = a_product(self.org, self.category, "Milk", "SKU-1", "50", self.branch)
        self.bread = a_product(self.org, self.category, "Bread", "SKU-2", "20", self.branch)
        self.clerk = a_person(self.org, "Ken Clerk", 1)
        self.manager = a_person(self.org, "Grace Manager", 2)

    # ── opening ─────────────────────────────────────────────────────────────

    def test_a_branch_may_only_have_one_count_open(self):
        """
        Two people counting the same shelves produce two contradictory truths
        and no way to say which was first.
        """
        services.open_count(branch=self.branch, staff=self.clerk)
        with self.assertRaises(ValidationError) as refusal:
            services.open_count(branch=self.branch, staff=self.manager)
        self.assertIn("already open", str(refusal.exception))

    def test_the_database_refuses_a_second_open_count_too(self):
        """
        The service check is the sentence; this is the guarantee. A second
        path to opening a count — a shell, a fixture, a future endpoint —
        must not be able to route around it.
        """
        services.open_count(branch=self.branch, staff=self.clerk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockCount.objects.create(
                    organization=self.org,
                    branch=self.branch,
                    number=99,
                    opened_by=self.clerk,
                )

    def test_a_closed_count_does_not_block_the_next_one(self):
        first = services.open_count(branch=self.branch, staff=self.clerk)
        services.close_count(count=first, staff=self.manager)
        second = services.open_count(branch=self.branch, staff=self.clerk)
        self.assertEqual(second.number, 2)

    def test_numbering_is_per_organisation(self):
        other_org, other_branch, _ = a_shop("Shop B")
        mine = services.open_count(branch=self.branch, staff=self.clerk)
        theirs = services.open_count(
            branch=other_branch, staff=a_person(other_org, "Zoe Other", 3)
        )
        self.assertEqual((mine.number, theirs.number), (1, 1))

    # ── counting ────────────────────────────────────────────────────────────

    def test_expected_is_the_system_figure_at_the_moment_of_counting(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        line = services.record_count(
            count=count, inventory=self.milk, counted="48", staff=self.clerk
        )
        self.assertEqual(line.expected_quantity, Decimal("50.00"))
        self.assertEqual(line.counted_quantity, Decimal("48.00"))
        self.assertEqual(line.variance, Decimal("-2.00"))

    def test_a_sale_during_the_count_is_not_a_discrepancy(self):
        """
        Count the shelf at 48 against a system that says 50 — a real shortfall
        of 2. Then the shop sells 10 more while the count is still open.

        The variance must stay -2. Reading the expected figure at close would
        make it -12 and report an afternoon's trade as missing stock.
        """
        count = services.open_count(branch=self.branch, staff=self.clerk)
        line = services.record_count(
            count=count, inventory=self.milk, counted="48", staff=self.clerk
        )

        services.adjust(
            inventory=self.milk, delta=Decimal("-10"), reason="OTHER", note="sold"
        )
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("40.00"))

        # Still -2. The line was true at the instant it was counted and the
        # sale has not retrospectively made the shelf wronger.
        line.refresh_from_db()
        self.assertEqual(line.variance, Decimal("-2.00"))

        services.close_count(count=count, staff=self.manager)
        self.milk.refresh_from_db()
        # 40 on the shelf, less the 2 the count found missing.
        self.assertEqual(self.milk.quantity, Decimal("38.00"))
        # The two answers this must not give:
        #   48 — overwriting the quantity with the counted figure, which
        #        would silently undo the ten that were sold.
        #   32 — deriving the variance at close against the live quantity
        #        (48 − 40 = +8 read as a shortfall of 8).
        self.assertNotEqual(self.milk.quantity, Decimal("48.00"))
        self.assertNotEqual(self.milk.quantity, Decimal("32.00"))

    def test_counting_the_same_shelf_twice_corrects_the_line(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="48", staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="49", staff=self.clerk)
        self.assertEqual(count.lines.count(), 1)
        self.assertEqual(count.lines.get().counted_quantity, Decimal("49.00"))

    def test_a_product_from_another_branch_is_invalid_input_not_a_refusal(self):
        """
        Reported against the field, like every other out-of-scope write in
        this codebase. It is a wrong line, not a forbidden one.
        """
        elsewhere = Branches.objects.create(
            organization=self.org, branch_name="Karen", branch_location="Nairobi",
            branch_allocation="1st", branch_manager="B", is_active=True,
        )
        theirs = a_product(self.org, self.category, "Sugar", "SKU-9", "5", elsewhere)
        count = services.open_count(branch=self.branch, staff=self.clerk)
        with self.assertRaises(ValidationError) as refusal:
            services.record_count(
                count=count, inventory=theirs, counted="5", staff=self.clerk
            )
        self.assertIn("inventory", refusal.exception.message_dict)

    def test_a_shelf_cannot_be_counted_as_less_than_nothing(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        with self.assertRaises(ValidationError):
            services.record_count(
                count=count, inventory=self.milk, counted="-1", staff=self.clerk
            )

    def test_nothing_moves_while_the_count_is_open(self):
        """
        A manager reviews the variances as a sheet before anything is written
        off. Applying each line as it is counted would make a stock take a
        stream of silent adjustments nobody ever saw whole.
        """
        count = services.open_count(branch=self.branch, staff=self.clerk)
        before = StockMovement.objects.count()
        services.record_count(count=count, inventory=self.milk, counted="10", staff=self.clerk)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("50.00"))
        self.assertEqual(StockMovement.objects.count(), before)

    # ── closing ─────────────────────────────────────────────────────────────

    def test_closing_moves_stock_through_a_movement(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="47", staff=self.clerk)
        closed, applied = services.close_count(count=count, staff=self.manager)

        self.assertEqual(applied, 1)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("47.00"))

        movement = StockMovement.objects.filter(inventory=self.milk).latest("id")
        self.assertEqual(movement.movement_type, "ADJUSTMENT")
        self.assertEqual(movement.quantity_before, Decimal("50.00"))
        self.assertEqual(movement.quantity_after, Decimal("47.00"))
        # The line points at the stock it moved.
        self.assertEqual(count.lines.get().movement_id, movement.id)

    def test_a_line_that_agreed_books_nothing(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="50", staff=self.clerk)
        before = StockMovement.objects.count()
        _, applied = services.close_count(count=count, staff=self.manager)
        self.assertEqual(applied, 0)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertIsNone(count.lines.get().movement_id)

    def test_a_surplus_is_booked_as_well_as_a_shortfall(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="52", staff=self.clerk)
        services.record_count(count=count, inventory=self.bread, counted="18", staff=self.clerk)
        _, applied = services.close_count(count=count, staff=self.manager)

        self.assertEqual(applied, 2)
        self.milk.refresh_from_db()
        self.bread.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("52.00"))
        self.assertEqual(self.bread.quantity, Decimal("18.00"))

    def test_closing_is_not_reversible(self):
        """
        A count that can be reopened is a count whose variance means nothing:
        the second number is always the one that agrees.
        """
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.close_count(count=count, staff=self.manager)
        with self.assertRaises(ValidationError):
            services.close_count(count=count, staff=self.manager)

    def test_a_closed_count_takes_no_more_lines(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.close_count(count=count, staff=self.manager)
        count.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.record_count(
                count=count, inventory=self.milk, counted="1", staff=self.clerk
            )

    def test_who_counted_and_who_closed_are_recorded_separately(self):
        """
        They are frequently and deliberately different people — see the note
        beside INVENTORY_COUNT_CLOSE.
        """
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="49", staff=self.clerk)
        closed, _ = services.close_count(count=count, staff=self.manager)
        self.assertEqual(closed.opened_by_id, self.clerk.id)
        self.assertEqual(closed.lines.get().counted_by_id, self.clerk.id)
        self.assertEqual(closed.closed_by_id, self.manager.id)

    # ── abandoning ──────────────────────────────────────────────────────────

    def test_abandoning_books_nothing_and_demands_a_reason(self):
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="1", staff=self.clerk)

        with self.assertRaises(ValidationError):
            services.abandon_count(count=count, staff=self.manager, reason="  ")

        abandoned = services.abandon_count(
            count=count, staff=self.manager, reason="Counted the wrong aisle"
        )
        self.assertEqual(abandoned.status, StockCount.Status.ABANDONED)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("50.00"))
        self.assertIn("Counted the wrong aisle", abandoned.note)

    # ── the sheet ───────────────────────────────────────────────────────────

    def test_the_summary_counts_lines_not_the_catalogue(self):
        """
        A partial count is the ordinary case — one aisle on a Tuesday — and
        reporting it against every product the branch stocks would make every
        count look unfinished.
        """
        a_product(self.org, self.category, "Rice", "SKU-3", "100", self.branch)
        count = services.open_count(branch=self.branch, staff=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="47", staff=self.clerk)
        services.record_count(count=count, inventory=self.bread, counted="22", staff=self.clerk)

        summary = services.count_summary(count)
        self.assertEqual(summary["counted"], 2)
        self.assertEqual(summary["short"], 1)
        self.assertEqual(summary["over"], 1)
        self.assertEqual(summary["agreed"], 0)
        self.assertEqual(summary["units_short"], Decimal("3.00"))
        self.assertEqual(summary["units_over"], Decimal("2.00"))
