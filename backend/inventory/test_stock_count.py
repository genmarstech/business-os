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
from unittest import mock

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
from identity.models import PlatformAccount
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
        services.open_count(branch=self.branch, actor=self.clerk)
        with self.assertRaises(ValidationError) as refusal:
            services.open_count(branch=self.branch, actor=self.manager)
        self.assertIn("already open", str(refusal.exception))

    def test_the_database_refuses_a_second_open_count_too(self):
        """
        The service check is the sentence; this is the guarantee. A second
        path to opening a count — a shell, a fixture, a future endpoint —
        must not be able to route around it.
        """
        services.open_count(branch=self.branch, actor=self.clerk)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockCount.objects.create(
                    organization=self.org,
                    branch=self.branch,
                    number=99,
                    opened_by_staff=self.clerk,
                )

    def test_a_closed_count_does_not_block_the_next_one(self):
        first = services.open_count(branch=self.branch, actor=self.clerk)
        services.close_count(count=first, actor=self.manager)
        second = services.open_count(branch=self.branch, actor=self.clerk)
        self.assertEqual(second.number, 2)

    def test_numbering_is_per_organisation(self):
        other_org, other_branch, _ = a_shop("Shop B")
        mine = services.open_count(branch=self.branch, actor=self.clerk)
        theirs = services.open_count(
            branch=other_branch, actor=a_person(other_org, "Zoe Other", 3)
        )
        self.assertEqual((mine.number, theirs.number), (1, 1))

    # ── counting ────────────────────────────────────────────────────────────

    def test_expected_is_the_system_figure_at_the_moment_of_counting(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        line = services.record_count(
            count=count, inventory=self.milk, counted="48", actor=self.clerk
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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        line = services.record_count(
            count=count, inventory=self.milk, counted="48", actor=self.clerk
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

        services.close_count(count=count, actor=self.manager)
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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="48", actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="49", actor=self.clerk)
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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        with self.assertRaises(ValidationError) as refusal:
            services.record_count(
                count=count, inventory=theirs, counted="5", actor=self.clerk
            )
        self.assertIn("inventory", refusal.exception.message_dict)

    def test_a_shelf_cannot_be_counted_as_less_than_nothing(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        with self.assertRaises(ValidationError):
            services.record_count(
                count=count, inventory=self.milk, counted="-1", actor=self.clerk
            )

    def test_nothing_moves_while_the_count_is_open(self):
        """
        A manager reviews the variances as a sheet before anything is written
        off. Applying each line as it is counted would make a stock take a
        stream of silent adjustments nobody ever saw whole.
        """
        count = services.open_count(branch=self.branch, actor=self.clerk)
        before = StockMovement.objects.count()
        services.record_count(count=count, inventory=self.milk, counted="10", actor=self.clerk)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("50.00"))
        self.assertEqual(StockMovement.objects.count(), before)

    # ── closing ─────────────────────────────────────────────────────────────

    def test_closing_moves_stock_through_a_movement(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="47", actor=self.clerk)
        closed, applied = services.close_count(count=count, actor=self.manager)

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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="50", actor=self.clerk)
        before = StockMovement.objects.count()
        _, applied = services.close_count(count=count, actor=self.manager)
        self.assertEqual(applied, 0)
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertIsNone(count.lines.get().movement_id)

    def test_a_surplus_is_booked_as_well_as_a_shortfall(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="52", actor=self.clerk)
        services.record_count(count=count, inventory=self.bread, counted="18", actor=self.clerk)
        _, applied = services.close_count(count=count, actor=self.manager)

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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.close_count(count=count, actor=self.manager)
        with self.assertRaises(ValidationError):
            services.close_count(count=count, actor=self.manager)

    def test_a_closed_count_takes_no_more_lines(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.close_count(count=count, actor=self.manager)
        count.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.record_count(
                count=count, inventory=self.milk, counted="1", actor=self.clerk
            )

    def test_who_counted_and_who_closed_are_recorded_separately(self):
        """
        They are frequently and deliberately different people — see the note
        beside INVENTORY_COUNT_CLOSE.
        """
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="49", actor=self.clerk)
        closed, _ = services.close_count(count=count, actor=self.manager)
        self.assertEqual(closed.opened_by_staff_id, self.clerk.id)
        self.assertEqual(closed.lines.get().counted_by_staff_id, self.clerk.id)
        self.assertEqual(closed.closed_by_staff_id, self.manager.id)

    # ── abandoning ──────────────────────────────────────────────────────────

    def test_abandoning_books_nothing_and_demands_a_reason(self):
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="1", actor=self.clerk)

        with self.assertRaises(ValidationError):
            services.abandon_count(count=count, actor=self.manager, reason="  ")

        abandoned = services.abandon_count(
            count=count, actor=self.manager, reason="Counted the wrong aisle"
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
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(count=count, inventory=self.milk, counted="47", actor=self.clerk)
        services.record_count(count=count, inventory=self.bread, counted="22", actor=self.clerk)

        summary = services.count_summary(count)
        self.assertEqual(summary["counted"], 2)
        self.assertEqual(summary["short"], 1)
        self.assertEqual(summary["over"], 1)
        self.assertEqual(summary["agreed"], 0)
        self.assertEqual(summary["units_short"], Decimal("3.00"))
        self.assertEqual(summary["units_over"], Decimal("2.00"))


# ═══════════════════════════════════════════════════════════════════════════
# WHO SIGNED IT
# ═══════════════════════════════════════════════════════════════════════════


class WhoSignedTheCountTests(TestCase):
    """
    A count is a control, and a control nobody signed is decoration.

    The service refuses an actor it cannot write down, rather than storing a
    count opened by nobody. procurement makes the opposite call for raising a
    draft order — deliberately, and the note on `services._actor` says why the
    two differ.
    """

    def setUp(self):
        self.org, self.branch, self.category = a_shop()
        self.milk = a_product(
            self.org, self.category, "Milk", "SKU-1", "50", self.branch
        )
        self.clerk = a_person(self.org, "Ken Clerk", 1)
        self.owner = PlatformAccount.objects.create(
            genmars_account_id=501,
            email="owner@shop-a.co.ke",
            full_name="Asha Owner",
        )

    def test_an_owner_with_no_staff_record_can_run_a_whole_count(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE SHAPE OF SHOP THIS IS SOLD INTO MOST OFTEN.

        A kiosk with one person in it has a PlatformAccount and no
        OrganizationStaff rows at all — nothing creates one, because
        `create_tenant` writes a TenantMembership and stops there. With a
        single staff-only actor column, every endpoint here demanded a
        personnel record that did not exist, so the owner of a one-person
        shop could not take a stock count. Not "awkwardly": at all.
        ══════════════════════════════════════════════════════════════════════
        """
        count = services.open_count(branch=self.branch, actor=self.owner)
        services.record_count(
            count=count, inventory=self.milk, counted="47", actor=self.owner
        )
        closed, applied = services.close_count(count=count, actor=self.owner)

        self.assertEqual(applied, 1)
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("47.00"))

        self.assertEqual(closed.opened_by_account_id, self.owner.pk)
        self.assertIsNone(closed.opened_by_staff_id)
        self.assertEqual(closed.opened_by_name, "Asha Owner")
        self.assertEqual(closed.closed_by_name, "Asha Owner")
        self.assertEqual(closed.lines.get().counted_by_name, "Asha Owner")

    def test_an_owner_signs_off_what_a_clerk_counted(self):
        """
        The arrangement the two permissions exist to make possible, and the
        one that was unreachable: the clerk is operational staff at the
        branch, the owner is a subscriber in the office, and the count is
        signed by both.
        """
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(
            count=count, inventory=self.milk, counted="48", actor=self.clerk
        )
        closed, _ = services.close_count(count=count, actor=self.owner)

        self.assertEqual(closed.opened_by_name, "Ken Clerk")
        self.assertEqual(closed.lines.get().counted_by_name, "Ken Clerk")
        self.assertEqual(closed.closed_by_name, "Asha Owner")

    def test_an_actor_of_neither_kind_is_refused(self):
        with self.assertRaises(ValidationError):
            services.open_count(branch=self.branch, actor=None)
        with self.assertRaises(ValidationError):
            services.open_count(branch=self.branch, actor="Ken")
        self.assertEqual(StockCount.objects.count(), 0)

    def test_an_unsignable_close_books_nothing(self):
        """
        Nothing moves, and the count stays open to be closed properly.

        ── THE `adjust` ASSERTION IS THE ONE DOING WORK ────────────────────
        The outcome alone is guaranteed by `@transaction.atomic` whatever
        order the signature is resolved in, so asserting only the quantity
        would pass against a `close_count` that books every variance and then
        rolls back. That is a different program with the same ending, and the
        difference matters: the next step somebody adds to this function —
        a receipt, a webhook, an email to the owner — will be put wherever
        the existing code suggests, and a rollback does not unsend an email.

        So the claim under test is that the refusal happens BEFORE the first
        movement is attempted, which is what patching `adjust` can see and
        the quantity cannot.
        """
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(
            count=count, inventory=self.milk, counted="40", actor=self.clerk
        )

        with mock.patch.object(services, "adjust") as booking:
            with self.assertRaises(ValidationError):
                services.close_count(count=count, actor=None)
        booking.assert_not_called()

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("50.00"))
        self.assertEqual(StockMovement.objects.count(), 0)
        count.refresh_from_db()
        self.assertEqual(count.status, StockCount.Status.OPEN)

    def test_recounting_a_shelf_moves_the_signature_to_whoever_recounted(self):
        """
        `update_or_create` defaults only overwrite the keys they name. With
        one actor column that was harmless; with two it would leave the first
        counter's name in the staff column beside the second counter's
        figure, and the line would read as signed by somebody who never saw
        it.
        """
        count = services.open_count(branch=self.branch, actor=self.clerk)
        services.record_count(
            count=count, inventory=self.milk, counted="47", actor=self.clerk
        )
        services.record_count(
            count=count, inventory=self.milk, counted="49", actor=self.owner
        )

        line = count.lines.get()
        self.assertEqual(line.counted_quantity, Decimal("49.00"))
        self.assertIsNone(line.counted_by_staff_id)
        self.assertEqual(line.counted_by_account_id, self.owner.pk)
        self.assertEqual(line.counted_by_name, "Asha Owner")

    def test_the_database_refuses_two_actors_on_one_event(self):
        """
        Both columns set is not an attribution, it is a question. The service
        cannot produce it; this is the guarantee against the shell, a
        fixture, and a future endpoint.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StockCount.objects.create(
                    organization=self.org,
                    branch=self.branch,
                    number=77,
                    opened_by_staff=self.clerk,
                    opened_by_account=self.owner,
                )
