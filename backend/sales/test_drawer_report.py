"""
Looking back at the drawers that were counted.

═══════════════════════════════════════════════════════════════════════════════
THE VARIANCE EXISTED AND WAS UNREADABLE, WHICH IS MOST OF THE WAY TO NOT
HAVING IT.

Closing a till computes a variance, raises a notification holding it, and
stores the count. After that it was gone: the only screen showing a drawer
figure was "Tills open now", which lists `status="OPEN"`, so a shift's figures
were visible for exactly as long as the shift had no variance and disappeared
at the moment it acquired one.

So the question a manager actually asks — "which drawers did not balance, and
is it always the same till" — had no answer anywhere in the product. A count
nobody can look back at only disciplines whoever happened to be watching.

── WHAT THESE TESTS ARE MOSTLY ABOUT ─────────────────────────────────────────

Not the arithmetic. `branches.services.drawer` owns that and
`branches/test_cash_movements.py` and `branches/tests.py` pin it. These are
about the four ways a summary over that arithmetic can tell a comfortable lie:

  · a shift closed before the count existed reading as "balanced"
  · a window that was truncated reading as the whole window
  · two tills cancelling out reading as a shop that balanced
  · another shop's drawers appearing in it
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from branches.models import (
    Branches,
    CashMovement,
    Register,
    RegisterShift,
    staffAssignment,
)
from branches import services as branch_services
from catalog.models import CatalogCategories, CatalogCategoryProduct, TaxRule
from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization, OrganizationStaff
from sales import reports, services as sales_services


class Shop:
    """One tenant, with a till, a cashier and something to sell."""

    def __init__(self, name, *, email_domain, ids):
        self.org = BusinessOrganization.objects.create(name=name)
        self.branch = Branches.objects.create(
            organization=self.org, branch_name=f"{name} Westlands",
            branch_location="Nairobi", branch_allocation="Ground floor",
            branch_manager="A Manager", is_active=True,
        )
        self.staff = OrganizationStaff.objects.create(
            organization=self.org, full_name="Jane", email=f"jane@{email_domain}",
            phone_number=f"+2547000{ids:05d}", address="Nairobi", id_number=ids,
        )
        staffAssignment.objects.create(
            staff_member=self.staff, branch=self.branch, staff_assignment="CA"
        )
        self.register = Register.objects.create(
            branch=self.branch, name="Till 1", register_number="T1"
        )

        rule = TaxRule.objects.create(
            organization=self.org, name="VAT 16%", rate=Decimal("16"),
            is_inclusive=True, is_default=True,
        )
        category = CatalogCategories.objects.create(
            organization=self.org, name="General"
        )
        self.product = CatalogCategoryProduct.objects.create(
            organization=self.org, category=category, name="Milk", sku="SKU-1",
            cost_price=Decimal("70.00"), selling_price=Decimal("100.00"),
            tax_rule=rule,
        )
        BranchInventory.objects.create(
            branch=self.branch, product=self.product, quantity=Decimal("500")
        )

        self.owner = PlatformAccount.objects.create(
            genmars_account_id=ids, email=f"owner@{email_domain}"
        )
        TenantMembership.objects.create(
            account=self.owner, organization=self.org,
            role=TenantMembership.Role.OWNER,
        )

    def open_till(self, *, opening="1000.00", name=None):
        register = self.register
        if name is not None:
            register = Register.objects.create(
                branch=self.branch, name=name, register_number=name
            )
        return RegisterShift.objects.create(
            register=register, operator=self.staff,
            opening_cash=Decimal(opening),
        )

    def sell(self, shift, *, cash):
        return sales_services.checkout(
            shift=shift, cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": "cash", "amount": Decimal(cash)}],
        )

    def count(self, shift, counted):
        """Close a till properly, through the service that counts it."""
        return branch_services.close_shift(shift=shift, counted_cash=Decimal(counted))

    def close_the_old_way(self, shift):
        """
        A shift closed the way shifts were closed before the count existed.

        `update()` rather than the service, on purpose: this is the row shape
        a real database already holds — CLOSED, with a close time written by
        nothing and `closing_cash` NULL. It cannot be produced through any
        code path that still exists, which is why it has to be forged here.
        """
        RegisterShift.objects.filter(pk=shift.pk).update(
            status="CLOSED", closed_at=timezone.now(), closing_cash=None
        )
        shift.refresh_from_db()
        return shift


class Base(TestCase):
    def setUp(self):
        self.shop = Shop("Shop A", email_domain="a.co.ke", ids=8201)
        self.user = self.shop.owner

    def window(self):
        now = timezone.now()
        return now - timedelta(days=1), now + timedelta(days=1)

    def report(self, **kwargs):
        start, end = self.window()
        return reports.drawers_counted(self.user, start, end, **kwargs)


class WhatTheSummarySaysTests(Base):
    def test_a_window_with_nothing_closed_is_an_empty_answer_not_an_error(self):
        answer = self.report()
        self.assertEqual(answer["shifts"], [])
        self.assertEqual(answer["summary"]["closed"], 0)
        self.assertEqual(answer["summary"]["net_variance"], Decimal("0.00"))
        self.assertFalse(answer["summary"]["truncated"])

    def test_a_balanced_drawer_is_reported_balanced(self):
        shift = self.shop.open_till()
        self.shop.sell(shift, cash="100.00")
        self.shop.count(shift, "1100.00")

        summary = self.report()["summary"]
        self.assertEqual(summary["counted"], 1)
        self.assertEqual(summary["balanced"], 1)
        self.assertEqual(summary["short"], 0)
        self.assertEqual(summary["net_variance"], Decimal("0.00"))

    def test_a_short_drawer_is_counted_short_and_carries_the_worst(self):
        first = self.shop.open_till()
        self.shop.count(first, "950.00")
        second = self.shop.open_till(name="Till 2")
        self.shop.count(second, "880.00")

        summary = self.report()["summary"]
        self.assertEqual(summary["short"], 2)
        self.assertEqual(summary["balanced"], 0)
        self.assertEqual(summary["net_variance"], Decimal("-170.00"))
        self.assertEqual(summary["worst_short"], Decimal("-120.00"))

    def test_two_tills_cancelling_out_is_not_a_shop_that_balanced(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE NUMBER THIS SCREEN COULD MOST EASILY LIE WITH.

        One drawer 500 short, another 500 over, and the net is zero. A screen
        reporting only the net would say the day reconciled — while one till
        is missing money and another has money nobody can account for, which
        is two problems rather than none.

        So `short` and `over` are carried beside the net, and the net is
        labelled as a net everywhere it is shown.
        ══════════════════════════════════════════════════════════════════════
        """
        short = self.shop.open_till()
        self.shop.count(short, "500.00")
        over = self.shop.open_till(name="Till 2")
        self.shop.count(over, "1500.00")

        summary = self.report()["summary"]
        self.assertEqual(summary["net_variance"], Decimal("0.00"))
        self.assertEqual(summary["short"], 1)
        self.assertEqual(summary["over"], 1)
        self.assertEqual(summary["balanced"], 0)
        self.assertEqual(summary["worst_short"], Decimal("-500.00"))

    def test_the_parts_always_add_up_to_the_whole(self):
        """
        `balanced + short + over == counted`, and `counted + uncounted ==
        closed`. A summary whose parts do not sum to its total is the fastest
        way to lose a manager's trust in every other figure on the screen.
        """
        self.shop.count(self.shop.open_till(), "1000.00")
        self.shop.count(self.shop.open_till(name="Till 2"), "900.00")
        self.shop.count(self.shop.open_till(name="Till 3"), "1100.00")
        self.shop.close_the_old_way(self.shop.open_till(name="Till 4"))

        answer = self.report()
        summary = answer["summary"]
        self.assertEqual(
            summary["balanced"] + summary["short"] + summary["over"],
            summary["counted"],
        )
        uncounted = sum(1 for row in answer["shifts"] if row["uncounted"])
        self.assertEqual(summary["counted"] + uncounted, summary["closed"])


class TheDrawerNobodyCountedTests(Base):
    def test_a_shift_closed_before_the_count_existed_is_not_balanced(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE MOST FLATTERING POSSIBLE LIE ABOUT A DRAWER.

        Before `close_shift`, a till was closed by PATCHing `status`. Those
        rows are CLOSED with `closing_cash` NULL, and a report that read a
        missing count as a zero variance would report every drawer from that
        era as having come out exactly right.
        ══════════════════════════════════════════════════════════════════════
        """
        shift = self.shop.close_the_old_way(self.shop.open_till())

        answer = self.report()
        row = answer["shifts"][0]
        self.assertEqual(row["shift"], shift.pk)
        self.assertTrue(row["uncounted"])
        self.assertIsNone(row["variance"])
        self.assertIsNone(row["counted_cash"])

        summary = answer["summary"]
        self.assertEqual(summary["closed"], 1, "it is still a closed shift")
        self.assertEqual(summary["counted"], 0, "and it was never counted")
        self.assertEqual(summary["balanced"], 0)
        self.assertEqual(summary["short"], 0)

    def test_an_uncounted_shift_does_not_move_the_net(self):
        self.shop.close_the_old_way(self.shop.open_till(opening="750.00"))
        summary = self.report()["summary"]
        self.assertEqual(summary["net_variance"], Decimal("0.00"))


class WhatIsInTheWindowTests(Base):
    def test_a_shift_is_placed_by_when_it_was_counted(self):
        """
        A night shift opens on Friday and is counted on Saturday morning. It
        belongs to the day somebody reconciled it — that is the day the money
        was handled and the day a manager goes looking.
        """
        shift = self.shop.open_till()
        self.shop.count(shift, "1000.00")

        RegisterShift.objects.filter(pk=shift.pk).update(
            opened_at=timezone.now() - timedelta(days=9),
        )

        self.assertEqual(self.report()["summary"]["closed"], 1)

    def test_a_drawer_counted_last_month_is_not_in_this_window(self):
        shift = self.shop.open_till()
        self.shop.count(shift, "900.00")
        RegisterShift.objects.filter(pk=shift.pk).update(
            closed_at=timezone.now() - timedelta(days=40)
        )

        answer = self.report()
        self.assertEqual(answer["shifts"], [])
        self.assertEqual(answer["summary"]["short"], 0, "nor its shortfall")

    def test_an_open_till_is_not_here_at_all(self):
        """It has no variance yet. "Tills open now" is the screen for it."""
        self.shop.open_till()
        self.assertEqual(self.report()["summary"]["closed"], 0)

    def test_newest_counted_first(self):
        first = self.shop.open_till()
        self.shop.count(first, "1000.00")
        second = self.shop.open_till(name="Till 2")
        self.shop.count(second, "1000.00")
        RegisterShift.objects.filter(pk=first.pk).update(
            closed_at=timezone.now() - timedelta(hours=5)
        )

        rows = self.report()["shifts"]
        self.assertEqual([row["shift"] for row in rows], [second.pk, first.pk])

    def test_more_shifts_than_the_limit_says_so_rather_than_cutting_quietly(self):
        """
        ══════════════════════════════════════════════════════════════════════
        "EVERY DRAWER BALANCED" AND "EVERY DRAWER WE LOOKED AT BALANCED" ARE
        DIFFERENT STATEMENTS.

        The summary describes the rows that were read, not the window. A
        truncated answer that did not say so would let a manager read a clean
        summary off a window holding a short drawer that was never fetched.
        ══════════════════════════════════════════════════════════════════════
        """
        for index in range(3):
            shift = self.shop.open_till(name=f"Till {index + 2}")
            self.shop.count(shift, "1000.00")

        start, end = self.window()
        answer = reports.drawers_counted(self.user, start, end, limit=2)

        self.assertEqual(len(answer["shifts"]), 2)
        self.assertEqual(answer["summary"]["closed"], 2, "what was read")
        self.assertEqual(answer["summary"]["closed_in_window"], 3, "what exists")
        self.assertTrue(answer["summary"]["truncated"])


class TheBreakdownTests(Base):
    def test_every_term_of_the_expected_figure_is_carried(self):
        """
        The reason the screen can be read at all: a manager looking at a
        drawer that does not add up sees WHY the expected figure is what it
        is, without opening anything else.
        """
        shift = self.shop.open_till()
        # A 200 note against a 100 sale: 200 across the counter, 100 back.
        self.shop.sell(shift, cash="200.00")
        branch_services.record_cash_movement(
            shift=shift, kind=CashMovement.Kind.FLOAT_IN,
            amount=Decimal("300.00"), reason="Change from the safe",
        )
        branch_services.record_cash_movement(
            shift=shift, kind=CashMovement.Kind.SAFE_DROP,
            amount=Decimal("500.00"), reason="Banked at lunchtime",
        )
        self.shop.count(shift, "900.00")

        row = self.report()["shifts"][0]
        self.assertEqual(row["opening_cash"], Decimal("1000.00"))
        self.assertEqual(row["cash_taken"], Decimal("200.00"), "handed over")
        self.assertEqual(row["change_given"], Decimal("100.00"), "handed back")
        self.assertEqual(row["paid_in"], Decimal("300.00"))
        self.assertEqual(row["paid_out"], Decimal("500.00"))
        self.assertEqual(row["refunded_cash"], Decimal("0.00"))
        self.assertEqual(row["expected_cash"], Decimal("900.00"))
        self.assertEqual(row["variance"], Decimal("0.00"))

    def test_the_terms_reconcile_to_the_expected_figure(self):
        """
        Asserted as arithmetic rather than as six constants, because the
        point of showing the breakdown is that somebody can add it up. A term
        that did not participate would make the screen unreadable in the one
        situation it exists for.
        """
        shift = self.shop.open_till()
        self.shop.sell(shift, cash="400.00")
        branch_services.record_cash_movement(
            shift=shift, kind=CashMovement.Kind.SAFE_DROP,
            amount=Decimal("250.00"), reason="Banked",
        )
        self.shop.count(shift, "1150.00")

        row = self.report()["shifts"][0]
        self.assertEqual(
            row["opening_cash"]
            + row["cash_taken"]
            - row["change_given"]
            + row["paid_in"]
            - row["paid_out"]
            - row["refunded_cash"],
            row["expected_cash"],
        )
        self.assertEqual(
            row["counted_cash"] - row["expected_cash"], row["variance"]
        )

    def test_a_cash_return_shows_as_a_return_and_not_as_a_shortfall(self):
        """
        The failure the till's Return screen was built to end: a manager used
        to record a return as a pay-out, which balanced the drawer and wrote
        no refund document. Here it has to appear as money given back.
        """
        shift = self.shop.open_till()
        sale = self.shop.sell(shift, cash="100.00")
        sales_services.refund_sale(
            sale=sale,
            branch=self.shop.branch,
            processed_by=self.shop.staff,
            reason="Wrong size",
            method="cash",
            lines=[{"sale_item": sale.items.first(), "quantity": Decimal("1")}],
            shift=shift,
        )
        self.shop.count(shift, "1000.00")

        row = self.report()["shifts"][0]
        self.assertEqual(row["refunded_cash"], Decimal("100.00"))
        self.assertEqual(row["expected_cash"], Decimal("1000.00"))
        self.assertEqual(row["variance"], Decimal("0.00"), "not 100 short")

    def test_the_operator_is_named_as_the_operator(self):
        """
        ⚠ NOT AS THE PERSON WHO COUNTED. Nothing records who closed a shift,
          and labelling the cashier as the counter would name the one person
          the permission model deliberately keeps out of the count.
        """
        shift = self.shop.open_till()
        self.shop.count(shift, "1000.00")

        row = self.report()["shifts"][0]
        self.assertEqual(row["operator_name"], "Jane")
        self.assertNotIn("counted_by", row)
        self.assertNotIn("closed_by", row)

    def test_the_shift_note_comes_with_it(self):
        """
        What the cashier wanted the manager to know is the first thing worth
        reading beside a shortfall, and it is frozen at close.
        """
        shift = self.shop.open_till()
        RegisterShift.objects.filter(pk=shift.pk).update(
            note="Customer took 50 for a bad milk, no receipt"
        )
        shift.refresh_from_db()
        self.shop.count(shift, "950.00")

        row = self.report()["shifts"][0]
        self.assertEqual(row["variance"], Decimal("-50.00"))
        self.assertIn("bad milk", row["note"])


class AnotherShopsDrawersTests(TestCase):
    def setUp(self):
        self.a = Shop("Shop A", email_domain="a.co.ke", ids=8301)
        self.b = Shop("Shop B", email_domain="b.co.ke", ids=8302)

    def window(self):
        now = timezone.now()
        return now - timedelta(days=1), now + timedelta(days=1)

    def test_a_shortfall_at_another_shop_is_not_in_this_one(self):
        """
        A report is the easiest place in a multi-tenant system to leak
        everything at once. This one is a query per shift, which makes the
        scoping easy to get wrong by iterating a queryset built somewhere
        else — so it is asserted on the figure, not just the row count.
        """
        mine = self.a.open_till()
        self.a.count(mine, "1000.00")

        theirs = self.b.open_till()
        self.b.count(theirs, "400.00")

        start, end = self.window()
        answer = reports.drawers_counted(self.a.owner, start, end)

        self.assertEqual([row["shift"] for row in answer["shifts"]], [mine.pk])
        self.assertEqual(answer["summary"]["short"], 0)
        self.assertEqual(answer["summary"]["net_variance"], Decimal("0.00"))
        self.assertEqual(answer["summary"]["closed_in_window"], 1)

    def test_naming_another_shops_branch_matches_nothing(self):
        """
        An explicit branch narrows, and is never widening: it is applied on
        top of the tenant confinement rather than instead of it.
        """
        self.b.count(self.b.open_till(), "400.00")

        start, end = self.window()
        answer = reports.drawers_counted(
            self.a.owner, start, end, branch_id=self.b.branch.pk
        )
        self.assertEqual(answer["shifts"], [])


class WhoMayReadItTests(TestCase):
    """
    The endpoint, and the principal it exists for.

    A branch auditor holds REPORTS_BRANCH and no till permission at all —
    they cannot open a shift, close one, or move cash. Somebody who can see
    that Friday is always short without being able to close a till is exactly
    who a reconciliation screen is for, and the first draft of this check
    would have refused them.
    """

    def setUp(self):
        self.shop = Shop("Shop A", email_domain="a.co.ke", ids=8401)
        self.shift = self.shop.open_till()
        self.shop.count(self.shift, "940.00")

    def as_staff(self, role, *, username):
        staff = OrganizationStaff.objects.create(
            organization=self.shop.org, full_name=f"Role {role}",
            email=f"{username}@a.co.ke", phone_number=f"+25471{hash(username) % 100000:05d}",
            address="Nairobi", id_number=abs(hash(username)) % 90000 + 9000,
        )
        staffAssignment.objects.create(
            staff_member=staff, branch=self.shop.branch, staff_assignment=role
        )
        credential = identity_services.issue_credential(
            staff=staff, username=username, password="not-a-real-password"
        )
        _, token = identity_services.open_staff_session(credential)
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_the_endpoint_is_not_open(self):
        response = self.client.get("/sls/reports/drawers/")
        self.assertIn(
            response.status_code, (401, 403), "/sls/reports/drawers/ was open"
        )

    def test_a_branch_auditor_may_read_it(self):
        response = self.client.get(
            "/sls/reports/drawers/", **self.as_staff("BA", username="auditor")
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["summary"]["short"], 1)

    def test_a_cashier_may_not(self):
        """
        A cashier does not hold REPORTS_BRANCH. Their own shift figures come
        from the till, and a list of every drawer's variance at the branch is
        a manager's view of them — see the note in identity/access.py.
        """
        response = self.client.get(
            "/sls/reports/drawers/", **self.as_staff("CA", username="cashier")
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_the_owner_may_read_it(self):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.shop.owner.pk
        session.save()

        response = self.client.get("/sls/reports/drawers/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_money_leaves_as_a_string_not_a_float(self):
        """
        DRF's encoder does float(obj). A variance is the one figure on this
        screen somebody will argue about, and -19.99 arriving as
        -19.989999999999998 is a figure nobody can argue about usefully.
        """
        self.shop.count(self.shop.open_till(name="Till 2"), "980.01")

        response = self.client.get(
            "/sls/reports/drawers/", **self.as_staff("BA", username="auditor")
        )
        body = response.json()
        for row in body["shifts"]:
            self.assertIsInstance(row["expected_cash"], str)
            self.assertIsInstance(row["variance"], str)
        self.assertIsInstance(body["summary"]["net_variance"], str)
        self.assertIn("-19.99", [row["variance"] for row in body["shifts"]])
