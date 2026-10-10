"""
What a lapsed subscription does, and — far more importantly — what it does
not.

The first class here is the one to read. If it ever goes red, somebody has
made a shop with an overdue invoice unable to serve a customer, and that is a
worse outcome than never collecting the invoice.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from branches.models import RegisterShift
from inventory.models import BranchInventory, StockMovement
from sales import services as sales_services
from sales.models import Payment, Sale
from subscriptions import entitlement, services
from subscriptions.models import State

from .factories import (
    a_plan,
    a_product,
    a_register,
    a_shop,
    a_staff,
    a_subscriber,
    assign,
)


class SellingNeverStopsTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════
    A SUSPENDED SUBSCRIPTION MUST NOT STOP A SHOP TRADING.

    A till that refuses a sale because an invoice is late is a shop with a
    queue at the counter and no way out of it from behind the till. The
    damage lands on a cashier and a customer, neither of whom is party to
    the arrangement, and it lands when the shop is busiest.

    Non-payment is a commercial problem between Genmars and the owner.
    These tests are the line that says so in code.
    ══════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.cashier = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        assign(self.cashier, self.branch)
        self.register = a_register(self.branch)
        self.shift = RegisterShift.objects.create(
            register=self.register,
            operator=self.cashier,
            opening_cash=Decimal("1000.00"),
        )
        self.product = a_product(self.org)
        BranchInventory.objects.create(
            branch=self.branch, product=self.product, quantity=Decimal("100")
        )

        # As lapsed as it gets: trial long over, grace long over.
        self.subscription = services.open_trial(self.org, days=0)
        self.subscription.trial_ends_on = timezone.localdate() - timedelta(days=365)
        self.subscription.save(update_fields=["trial_ends_on"])
        self.assertEqual(self.subscription.state(), State.SUSPENDED)

    def sell(self):
        return sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.product, "quantity": Decimal("2")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
        )

    def test_a_suspended_shop_can_still_sell(self):
        sale = self.sell()
        self.assertEqual(sale.status, Sale.Status.COMPLETED)

    def test_a_suspended_shop_can_still_refund(self):
        sale = self.sell()
        refund = sales_services.refund_sale(
            sale=sale,
            branch=self.branch,
            processed_by=self.cashier,
            lines=[{"sale_item": sale.items.first(), "quantity": Decimal("1")}],
            reason="Customer changed their mind",
            method=Payment.Method.CASH,
        )
        self.assertIsNotNone(refund.pk)

    def test_a_suspended_shop_can_still_move_stock(self):
        self.sell()
        self.assertTrue(
            StockMovement.objects.filter(
                inventory__branch=self.branch, inventory__product=self.product
            ).exists()
        )

    def test_the_entitlement_summary_says_so_out_loud(self):
        """
        Not left for a client to infer from the state. The one sentence the
        banner exists to carry is a field, so no frontend can render
        "suspended" over a working till without being told otherwise.
        """
        summary = entitlement.summary(self.org.pk)
        self.assertEqual(summary["state"], State.SUSPENDED)
        self.assertTrue(summary["selling_continues"])


class GrowthTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.subscription = services.open_trial(self.org)

    def suspend(self):
        self.subscription.trial_ends_on = timezone.localdate() - timedelta(days=365)
        self.subscription.save(update_fields=["trial_ends_on"])

    def test_a_tenant_with_no_subscription_row_is_not_narrowed(self):
        """
        ── THE DEPLOY-DAY TEST ────────────────────────────────────────────
        Every organisation that existed before this app has no row. Reading
        the absence as unpaid would suspend the entire customer base the
        moment this ships, which is a new feature behaving as an outage.
        """
        other, _ = a_shop("No Subscription Ltd")
        self.assertEqual(entitlement.state_of(other.pk), State.ACTIVE)
        self.assertTrue(entitlement.may_add(other.pk, entitlement.BRANCH).allowed)

    def test_a_suspended_tenant_cannot_add_a_branch(self):
        self.suspend()
        verdict = entitlement.may_add(self.org.pk, entitlement.BRANCH)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "subscription_inactive")
        # The refusal has to say what still works, in the same breath.
        self.assertIn("untouched", verdict.message)

    def test_grace_does_not_narrow_anything(self):
        """
        If it did, the grace period would be a formality. The point of it is
        that a late invoice does not become an operational problem.
        """
        self.subscription.trial_ends_on = timezone.localdate() - timedelta(days=1)
        self.subscription.grace_days = 14
        self.subscription.save(update_fields=["trial_ends_on", "grace_days"])

        self.assertEqual(self.subscription.state(), State.PAST_DUE)
        self.assertTrue(entitlement.may_add(self.org.pk, entitlement.BRANCH).allowed)

    def test_a_null_limit_is_no_limit_and_not_a_limit_of_zero(self):
        """
        The same trap as `branch_scope` returning None for unrestricted
        authority: a falsy check locks the largest customer out entirely.
        """
        self.subscription.plan = a_plan(code="unlimited", branch_limit=None)
        self.subscription.save(update_fields=["plan"])
        self.assertTrue(entitlement.may_add(self.org.pk, entitlement.BRANCH).allowed)

    def test_a_plan_at_its_branch_limit_refuses_the_next_one(self):
        self.subscription.plan = a_plan(code="starter", name="Starter", branch_limit=1)
        self.subscription.save(update_fields=["plan"])

        verdict = entitlement.may_add(self.org.pk, entitlement.BRANCH)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "plan_limit")
        self.assertEqual(verdict.limit, 1)
        self.assertEqual(verdict.current, 1)
        # An upsell and a debt read differently, and are sent to different
        # places by a screen.
        self.assertIn("Starter", verdict.message)

    def test_a_trial_with_no_plan_has_no_ceiling_to_enforce(self):
        """Inventing one would invent terms nobody agreed to."""
        self.assertIsNone(self.subscription.plan)
        self.assertTrue(entitlement.may_add(self.org.pk, entitlement.BRANCH).allowed)

    def test_an_inactive_branch_does_not_occupy_a_seat(self):
        self.subscription.plan = a_plan(code="starter", branch_limit=1)
        self.subscription.save(update_fields=["plan"])
        self.branch.is_active = False
        self.branch.save(update_fields=["is_active"])
        self.assertTrue(entitlement.may_add(self.org.pk, entitlement.BRANCH).allowed)

    def test_staff_are_counted_by_who_works_here_not_by_rows_ever_written(self):
        """
        A shop that has been through thirty cashiers in two years must not be
        permanently at the limit of a plan that allows fifteen, with no way
        back under it short of deleting people from an employment record.
        """
        self.subscription.plan = a_plan(code="starter", staff_limit=2)
        self.subscription.save(update_fields=["plan"])

        for number in range(1, 6):
            staff = a_staff(
                self.org,
                name=f"Former {number}",
                email=f"former{number}@a.co.ke",
                id_number=2000 + number,
            )
            assignment = assign(staff, self.branch)
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])

        working = a_staff(
            self.org, name="Current", email="current@a.co.ke", id_number=3001
        )
        assign(working, self.branch)

        self.assertTrue(entitlement.may_add(self.org.pk, entitlement.STAFF).allowed)

    def test_a_growth_name_nobody_defined_is_a_programming_error(self):
        """
        Returning "allowed" for an unknown action would make a typo in a
        viewset's `grows` silently switch the limit off.
        """
        with self.assertRaises(ValueError):
            entitlement.may_add(self.org.pk, "catalogue")
