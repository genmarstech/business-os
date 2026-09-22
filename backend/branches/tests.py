"""
Closing a till, and the count that has to happen first.

══════════════════════════════════════════════════════════════════════════════
THE BUG THESE PIN IS AN ABSENCE, WHICH IS THE HARDEST KIND TO NOTICE.

RegisterShift carried `closing_cash` and `closed_at` from the day it was
written and nothing ever set either. Closing a till was a PATCH that moved
`status` to CLOSED — no close time, no counted amount, and therefore no
variance. Every existing test passed, every screen looked right, and a shop
could have traded for a year without once discovering a short drawer.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct, TaxRule
from identity import access, services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization, OrganizationStaff
from sales import services as sales_services


class ClosingATillTests(TestCase):
    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Shop A")
        self.branch = Branches.objects.create(
            organization=self.org, branch_name="Westlands",
            branch_location="Nairobi", branch_allocation="Ground floor",
            branch_manager="A Manager", is_active=True,
        )
        self.jane = OrganizationStaff.objects.create(
            organization=self.org, full_name="Jane", email="jane@a.co.ke",
            phone_number="+254700000001", address="Nairobi", id_number=8001,
        )
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="CA"
        )
        self.register = Register.objects.create(
            branch=self.branch, name="Till 1", register_number="T1"
        )
        self.shift = RegisterShift.objects.create(
            register=self.register, operator=self.jane,
            opening_cash=Decimal("1000.00"),
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
            branch=self.branch, product=self.product, quantity=Decimal("50")
        )

        self.owner = PlatformAccount.objects.create(
            genmars_account_id=8100, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(
            account=self.owner, organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()

    def sell(self, *, cash: str, change: str = "0.00"):
        return sales_services.checkout(
            shift=self.shift, cashier=self.jane,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": "cash", "amount": Decimal(cash)}],
        )

    # ── what the drawer should hold ──────────────────────────────────────

    def test_an_untraded_shift_expects_its_opening_float(self):
        response = self.client.get(f"/brn/register-shifts/{self.shift.pk}/drawer/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["expected_cash"], "1000.00")
        self.assertEqual(body["cash_taken"], "0.00")

    def test_a_sale_adds_its_cash_to_what_is_expected(self):
        self.sell(cash="100.00")
        body = self.client.get(
            f"/brn/register-shifts/{self.shift.pk}/drawer/"
        ).json()
        self.assertEqual(body["cash_taken"], "100.00")
        self.assertEqual(body["expected_cash"], "1100.00")

    def test_an_uncounted_drawer_has_no_variance_rather_than_a_zero_one(self):
        """
        Reporting 0 for a drawer nobody has counted would be the most
        misleading number on the screen — it reads as "checked, and correct".
        """
        body = self.client.get(
            f"/brn/register-shifts/{self.shift.pk}/drawer/"
        ).json()
        self.assertIsNone(body["variance"])
        self.assertIsNone(body["counted_cash"])

    # ── closing ──────────────────────────────────────────────────────────

    def test_closing_records_the_count_the_time_and_a_variance(self):
        self.sell(cash="100.00")
        response = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "1080.00"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        drawer = response.json()["drawer"]
        self.assertEqual(drawer["expected_cash"], "1100.00")
        self.assertEqual(drawer["counted_cash"], "1080.00")
        self.assertEqual(drawer["variance"], "-20.00", "20 short")

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.status, "CLOSED")
        self.assertEqual(self.shift.closing_cash, Decimal("1080.00"))
        self.assertIsNotNone(
            self.shift.closed_at, "a closed shift with no close time is the bug"
        )

    def test_an_over_drawer_is_positive(self):
        self.sell(cash="100.00")
        body = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "1130.00"},
            content_type="application/json",
        ).json()
        self.assertEqual(body["drawer"]["variance"], "30.00")

    def test_the_count_is_required(self):
        response = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.status, "OPEN")

    def test_a_till_cannot_be_closed_twice(self):
        """
        The second count is the one that agrees. A shift that can be re-closed
        has a variance that means nothing.
        """
        first = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "900.00"}, content_type="application/json",
        )
        self.assertEqual(first.status_code, 200, first.content)

        second = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "1000.00"}, content_type="application/json",
        )
        self.assertEqual(second.status_code, 400, second.content)

        self.shift.refresh_from_db()
        self.assertEqual(
            self.shift.closing_cash, Decimal("900.00"), "the first count stands"
        )

    def test_a_negative_count_is_refused(self):
        response = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "-5"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    # ── the shortcut that used to exist ──────────────────────────────────

    def test_patching_status_no_longer_closes_a_till(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE WHOLE POINT. This was the path everything took, and it produced
        a CLOSED shift with no close time and no count.

        A service the client can route around is not a service.
        ══════════════════════════════════════════════════════════════════
        """
        response = self.client.patch(
            f"/brn/register-shifts/{self.shift.pk}/",
            {"status": "CLOSED", "closing_cash": "999.00"},
            content_type="application/json",
        )
        self.assertIn(response.status_code, (200, 400), response.content)

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.status, "OPEN", "PATCH must not close it")
        self.assertIsNone(self.shift.closing_cash, "nor set a count")
        self.assertIsNone(self.shift.closed_at)

    def test_a_closed_till_leaves_the_open_list(self):
        """register_status drives "Tills open now" — a closed one must go."""
        from sales import reports

        self.assertEqual(len(reports.register_status(self.owner)), 1)
        self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "1000.00"}, content_type="application/json",
        )
        self.assertEqual(len(reports.register_status(self.owner)), 0)

    def test_the_report_and_the_close_agree_on_what_is_expected(self):
        """
        Two implementations of "opening + cash taken − change given" is two
        places for the end-of-day figure to disagree with the dashboard, and
        the wrong one is whichever the manager is not looking at.
        """
        from sales import reports

        self.sell(cash="100.00")
        self.sell(cash="100.00")

        from branches import services as branch_services

        reported = reports.register_status(self.owner)[0]["expected_cash"]
        computed = branch_services.drawer(self.shift)["expected_cash"]
        self.assertEqual(Decimal(str(reported)), computed)


class WhoMayCloseTests(TestCase):
    """A cashier counts the drawer; a manager closes against the count."""

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

    def test_a_cashier_may_see_what_the_drawer_should_hold(self):
        """They are the one holding the notes. Seeing is not closing."""
        response = self.client.get(
            f"/brn/register-shifts/{self.shift.pk}/drawer/", **self.till()
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["expected_cash"], "1000.00")

    def test_a_cashier_may_not_close_their_own_till(self):
        """
        Counting your own drawer and declaring it correct is the shape a till
        shortage takes. SHIFT_CLOSE is a manager's — identity/access.py.
        """
        response = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "700.00"},
            content_type="application/json",
            **self.till(),
        )
        self.assertEqual(response.status_code, 403, response.content)

        self.shift.refresh_from_db()
        self.assertEqual(self.shift.status, "OPEN")
        self.assertIsNone(self.shift.closing_cash)

    def test_a_manager_may(self):
        """The positive control. Without it the refusal proves nothing."""
        staffAssignment.objects.create(
            staff_member=self.jane, branch=self.branch, staff_assignment="AM"
        )
        response = self.client.post(
            f"/brn/register-shifts/{self.shift.pk}/close/",
            {"counted_cash": "1000.00"},
            content_type="application/json",
            **self.till(),
        )
        self.assertEqual(response.status_code, 200, response.content)
