"""
Cash in and out of a drawer between opening it and counting it.

═══════════════════════════════════════════════════════════════════════════════
WITHOUT THIS THE VARIANCE LIES ON ANY DAY SOMEBODY BANKS THE TAKINGS.

Expected cash was opening float plus cash taken less change given. A shop that
lifts KSh 5,000 out at lunchtime to walk it to the bank then counts a drawer
5,000 below what the system expects, and the till reports it SHORT — for doing
the most ordinary thing a cash business does.

Worse since notifications shipped: a short drawer raises one to every holder of
`reports.branch`. So the cost was not a wrong figure on a screen somebody might
not look at. It was a manager paged, daily, about money nobody lost.

── AND THE PERMISSION SPLIT IS THE WHOLE CONTROL ──────────────────────────────

A PAY-IN raises the expected figure, so a false one makes the drawer look more
short, never less. A cashier may record their own.

A PAY-OUT lowers it. A cashier who could record one could take money and write
the shortfall away in the same movement — which is exactly what a drawer count
exists to catch. The tests below are mostly about that asymmetry.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from branches.models import (
    Branches,
    CashMovement,
    Register,
    RegisterShift,
    staffAssignment,
)
from branches import services
from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, TenantMembership
from organisations.models import BusinessOrganization, OrganizationStaff


class Base(TestCase):
    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Shop A")
        self.branch = Branches.objects.create(
            organization=self.org, branch_name="Westlands",
            branch_location="Nairobi", branch_allocation="Ground floor",
            branch_manager="A Manager", is_active=True,
        )
        self.jane = OrganizationStaff.objects.create(
            organization=self.org, full_name="Jane", email="jane@a.co.ke",
            phone_number="+254700000002", address="Nairobi", id_number=8002,
        )
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        credential = identity_services.issue_credential(
            staff=self.jane, username="jane", password="not-a-real-password"
        )
        _, self.token = identity_services.open_staff_session(credential)

        self.register = Register.objects.create(
            branch=self.branch, name="Till 1", register_number="T1"
        )
        self.shift = RegisterShift.objects.create(
            register=self.register, operator=self.jane,
            opening_cash=Decimal("1000.00"),
        )

    def till(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def make_manager(self):
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="AM"
        )

    def post_cash(self, kind, amount, reason="Banked at lunchtime", *, auth=None):
        """
        `auth=None` means the till's bearer token; pass `auth={}` for whatever
        the client's cookie jar holds, which is how the subscriber case signs
        in. Explicit rather than a `**extra` that silently falls back — a test
        that authenticates as somebody other than it claims proves nothing,
        and the first draft of this did exactly that.
        """
        return self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/cash/",
            {"kind": kind, "amount": amount, "reason": reason},
            content_type="application/json",
            **(self.till() if auth is None else auth),
        )

    def expected(self):
        return services.drawer(self.shift)["expected_cash"]


class TheExpectedFigureTests(Base):
    def test_money_banked_mid_shift_lowers_what_the_drawer_should_hold(self):
        """
        The whole point. Before this, the drawer was expected to still hold
        the 5,000 that was walked to the bank.
        """
        self.assertEqual(self.expected(), Decimal("1000.00"))
        self.make_manager()

        self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00")

        self.assertEqual(self.expected(), Decimal("600.00"))

    def test_change_brought_in_raises_it(self):
        self.post_cash(CashMovement.Kind.FLOAT_IN, "500.00", reason="Change from the safe")
        self.assertEqual(self.expected(), Decimal("1500.00"))

    def test_a_banked_drawer_reconciles_instead_of_reading_short(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE FALSE ALARM THIS CLOSES.

        Open with 1,000. Bank 400. Count 600. That drawer is CORRECT, and
        before this it reported 400 short — and, since notifications shipped,
        raised one to every holder of `reports.branch`.
        ══════════════════════════════════════════════════════════════════════
        """
        self.make_manager()
        self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00")

        services.close_shift(shift=self.shift, counted_cash=Decimal("600.00"))

        self.shift.refresh_from_db()
        self.assertEqual(services.drawer(self.shift)["variance"], Decimal("0.00"))

    def test_a_genuine_shortfall_still_shows(self):
        """
        The control. A change that made every drawer reconcile would have
        removed the one number a till exists to produce.
        """
        self.make_manager()
        self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00")

        services.close_shift(shift=self.shift, counted_cash=Decimal("550.00"))

        self.shift.refresh_from_db()
        self.assertEqual(services.drawer(self.shift)["variance"], Decimal("-50.00"))

    def test_the_two_directions_are_reported_separately(self):
        """
        So a manager looking at a drawer that does not add up can see WHY the
        expected figure is what it is, without opening another screen.
        """
        self.make_manager()
        self.post_cash(CashMovement.Kind.FLOAT_IN, "200.00", reason="Change")
        self.post_cash(CashMovement.Kind.SAFE_DROP, "500.00")

        counts = services.drawer(self.shift)
        self.assertEqual(counts["paid_in"], Decimal("200.00"))
        self.assertEqual(counts["paid_out"], Decimal("500.00"))
        self.assertEqual(counts["expected_cash"], Decimal("700.00"))


class WhoMayMoveCashTests(Base):
    def test_a_cashier_may_bring_change_in(self):
        """
        A pay-in only ever makes the drawer look MORE short, so it hides
        nothing and needs nobody else.
        """
        response = self.post_cash(
            CashMovement.Kind.FLOAT_IN, "500.00", reason="Change from the safe"
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_cashier_may_not_take_cash_out(self):
        """
        ⚠ THE CONTROL. A cashier who could record a pay-out could take money
          and write the shortfall away in the same movement.
        """
        for kind in (CashMovement.Kind.SAFE_DROP, CashMovement.Kind.PAY_OUT):
            with self.subTest(kind=kind):
                response = self.post_cash(kind, "400.00")
                self.assertEqual(response.status_code, 403, response.content)

        self.assertEqual(CashMovement.objects.count(), 0)
        self.assertEqual(self.expected(), Decimal("1000.00"))

    def test_a_manager_may(self):
        """The positive control — "refused" must not mean "broken for all"."""
        self.make_manager()
        response = self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00")
        self.assertEqual(response.status_code, 201, response.content)

    def test_an_owner_may(self):
        """A subscriber holds every permission, and is not an OrganizationStaff."""
        account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account, organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

        response = self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00", auth={})
        self.assertEqual(response.status_code, 201, response.content)

        movement = CashMovement.objects.get()
        self.assertEqual(movement.recorded_by_account, account)
        self.assertIsNone(movement.recorded_by_staff)


class WhatIsRefusedTests(Base):
    def setUp(self):
        super().setUp()
        self.make_manager()

    def test_a_reason_is_required(self):
        """
        "Why is there 5,000 less in this drawer" is the entire question the
        row exists to answer.
        """
        response = self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00", reason="   ")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CashMovement.objects.count(), 0)

    def test_a_negative_amount_is_refused(self):
        """
        The direction is the kind. A negative amount would be a second way to
        express it, and the two would eventually disagree.
        """
        response = self.post_cash(CashMovement.Kind.SAFE_DROP, "-400.00")
        self.assertEqual(response.status_code, 400, response.content)

    def test_zero_is_refused(self):
        response = self.post_cash(CashMovement.Kind.FLOAT_IN, "0.00")
        self.assertEqual(response.status_code, 400, response.content)

    def test_an_unknown_kind_is_refused(self):
        response = self.post_cash("borrowed_it", "400.00")
        self.assertEqual(response.status_code, 400, response.content)

    def test_cash_cannot_move_through_a_closed_shift(self):
        """
        ⚠ A movement against a closed shift changes the expected figure AFTER
          somebody counted against it — rewriting a variance that has already
          been signed off, which is the one number the close exists to
          produce.
        """
        services.close_shift(shift=self.shift, counted_cash=Decimal("1000.00"))

        response = self.post_cash(CashMovement.Kind.SAFE_DROP, "400.00")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(CashMovement.objects.count(), 0)


class AttributionTests(Base):
    def test_a_movement_records_who_made_it(self):
        response = self.post_cash(
            CashMovement.Kind.FLOAT_IN, "500.00", reason="Change from the safe"
        )
        self.assertEqual(response.status_code, 201, response.content)

        movement = CashMovement.objects.get()
        self.assertEqual(movement.recorded_by_staff, self.jane)
        self.assertIsNone(movement.recorded_by_account)

    def test_the_response_carries_the_redrawn_drawer(self):
        """So the till does not have to ask again for a figure it just changed."""
        body = self.post_cash(
            CashMovement.Kind.FLOAT_IN, "500.00", reason="Change"
        ).json()
        self.assertEqual(body["drawer"]["expected_cash"], "1500.00")
        self.assertEqual(body["movement"]["reason"], "Change")


class EveryKindHasADirectionTests(TestCase):
    """
    ⚠ A KIND IN NEITHER SET COUNTS AS NOTHING IN THE EXPECTED FIGURE.

    It would be silent: no error, no log, just a drawer that will not
    reconcile for a reason nobody can see. Adding a kind means adding it to
    INWARD or OUTWARD, and this fails if somebody forgets.
    """

    def test_every_kind_is_inward_or_outward(self):
        sorted_kinds = sorted(CashMovement.Kind.values)
        covered = sorted(set(CashMovement.INWARD) | set(CashMovement.OUTWARD))
        self.assertEqual(
            sorted_kinds, covered,
            "A CashMovement.Kind is in neither INWARD nor OUTWARD, so it "
            "would move no money in the expected figure.",
        )

    def test_the_two_sets_do_not_overlap(self):
        self.assertEqual(
            set(CashMovement.INWARD) & set(CashMovement.OUTWARD), set()
        )


class TheDashboardAndTheCloseScreenAgreeTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════════
    `drawer()` CLAIMED "ONE IMPLEMENTATION, SHARED WITH THE REPORT" AND IT WAS
    NOT SHARED.

    sales/reports.register_status held a second copy of
    `opening + cash taken − change given`. The two stayed in step only because
    nobody had changed either — and then cash movements were added to one.

    A shop that banked its takings at lunchtime would have seen the close
    screen expect one figure and the dashboard expect another, several hundred
    shillings apart, with nothing to say which was lying. That is the exact
    failure the banner describes, reached the exact way it predicted.

    This test is what makes the claim true rather than aspirational.
    ══════════════════════════════════════════════════════════════════════════
    """

    def test_register_status_reports_the_same_expected_cash_as_the_close(self):
        from sales import reports

        self.make_manager()
        self.post_cash(CashMovement.Kind.FLOAT_IN, "300.00", reason="Change")
        self.post_cash(CashMovement.Kind.SAFE_DROP, "500.00")

        account = PlatformAccount.objects.create(
            genmars_account_id=2, email="owner2@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account, organization=self.org,
            role=TenantMembership.Role.OWNER,
        )

        row = next(
            r for r in reports.register_status(account) if r["shift"] == self.shift.pk
        )
        counts = services.drawer(self.shift)

        for field in (
            "opening_cash", "cash_taken", "change_given",
            "paid_in", "paid_out", "expected_cash",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    row[field], counts[field],
                    f"the dashboard and the close screen disagree about {field}",
                )
        self.assertEqual(row["expected_cash"], Decimal("800.00"))
