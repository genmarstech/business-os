"""
The counting endpoints, through HTTP, where a real caller stands.

═══════════════════════════════════════════════════════════════════════════════
THE ONE THAT MATTERS: test_cannot_open_a_count_in_another_shops_branch.

It failed when it was written. `/invt/stock-counts/open/` answered 201 to a
caller naming a branch in a business they cannot read, writing a row into that
tenant — and taking its one permitted open count, so the shop that owned the
branch could not start a stock take of their own and would find one they never
began, signed by one of their own employees.

`TenantScoped` guards writes in `create()` and `update()`. This endpoint is a
custom action and reaches neither. Every other `detail=False` POST in this
codebase — `sales.checkout`, `payments.request` — does the check by hand; this
one did not, and the service tests it shipped with never made an HTTP request,
so nothing noticed.
═══════════════════════════════════════════════════════════════════════════════

The positive control sits beside each refusal, as it does in
procurement/tests/test_http.py: "refused" must not be able to mean "broken for
everybody".
"""

from __future__ import annotations

import json
from decimal import Decimal

from branches.models import Branches, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from django.test import TestCase

from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory.models import BranchInventory, StockCount
from organisations.models import BusinessOrganization, OrganizationStaff

PASSWORD = "till-password-not-real"


def a_shop(name, *, branches=("Main",)):
    org = BusinessOrganization.objects.create(name=name)
    made = [
        Branches.objects.create(
            organization=org,
            branch_name=f"{name} {branch}",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="A Manager",
            is_active=True,
        )
        for branch in branches
    ]
    return org, made


def a_person(org, name, n):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=f"person{n}@example.co.ke",
        phone_number=f"+2547{n:08d}",
        address="Nairobi",
        id_number=n,
    )


def stock(org, branch, name, sku, quantity):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
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


class Base(TestCase):
    def sign_in_subscriber(self, account):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

    def sign_in_staff(self, staff, username):
        credential = StaffCredential(staff=staff, username=username)
        credential.set_password(PASSWORD)
        credential.save()
        _, token = identity_services.open_staff_session(credential)
        self.token = token

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}


class TenantIsolationTests(Base):
    """Shop A must not be able to count Shop B's shelves."""

    def setUp(self):
        self.a, (self.a_main,) = a_shop("Shop A")
        self.b, (self.b_main,) = a_shop("Shop B")
        self.staff_b = a_person(self.b, "Bee Clerk", 2)

        self.owner_a = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop-a.co.ke", full_name="Asha Owner"
        )
        TenantMembership.objects.create(
            account=self.owner_a,
            organization=self.a,
            role=TenantMembership.Role.OWNER,
        )
        self.sign_in_subscriber(self.owner_a)

    def test_opening_a_count_in_their_own_branch_succeeds(self):
        """
        The control. Without it a 400 from a missing field would read as
        isolation working — a negative test passing for a reason that has
        nothing to do with what it claims.

        It also pins the other half of this change: the owner has no
        OrganizationStaff row and never will, and before the account column
        existed this answered 400 for want of an `opened_by`.
        """
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.a_main.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["opened_by_name"], "Asha Owner")

        count = StockCount.objects.get()
        self.assertEqual(count.organization_id, self.a.pk)
        self.assertEqual(count.opened_by_account_id, self.owner_a.pk)

    def test_cannot_open_a_count_in_another_shops_branch(self):
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.b_main.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        # 400 and not 403: "not yours" and "does not exist" have to read
        # identically, or the refusal enumerates the other tenant one id at
        # a time. identity/scoping.py has the argument.
        self.assertIn("branch", response.json())
        self.assertFalse(StockCount.objects.exists())

    def test_naming_another_shops_employee_does_not_attribute_the_count(self):
        """
        `opened_by` used to be read from the body out of an unfiltered
        queryset. A count signed by a stranger's employee also PROTECTs that
        row, so the shop that employs them could no longer remove them — and
        the error naming the obstruction points at a count in a tenant they
        cannot see.

        The field is gone. Sending it is now ignored rather than refused,
        which is what an unknown key in a DRF Serializer does, and the
        assertion is that the signature is the CALLER either way.
        """
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.a_main.pk, "opened_by": self.staff_b.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        count = StockCount.objects.get()
        self.assertIsNone(count.opened_by_staff_id)
        self.assertEqual(count.opened_by_account_id, self.owner_a.pk)


class BranchScopeTests(Base):
    """
    One tenant, two branches. Holding `inventory.count` is not consent to
    count everywhere the business trades.
    """

    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Shop A", branches=("Westlands", "Karen")
        )
        self.clerk = a_person(self.org, "Ken Clerk", 1)
        staffAssignment.objects.create(
            staff_member=self.clerk, branch=self.westlands, staff_assignment="IC"
        )
        self.sign_in_staff(self.clerk, "ken")

    def test_a_clerk_may_count_the_branch_they_are_assigned_to(self):
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.westlands.pk},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["opened_by_name"], "Ken Clerk")

    def test_a_clerk_may_not_count_a_branch_they_are_not_assigned_to(self):
        """
        403 here, unlike the tenant refusal above, and the difference is
        deliberate: the caller can already read Karen — it is their own
        employer's branch — so there is nothing to conceal. Telling them they
        are not assigned to it is the answer they can act on.
        """
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.karen.pk},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(StockCount.objects.exists())


class AttributionTests(Base):
    """Every endpoint signs with the session, and none of them with the body."""

    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")
        self.milk = stock(self.org, self.branch, "Milk", "SKU-1", "50")
        self.clerk = a_person(self.org, "Ken Clerk", 1)
        self.manager = a_person(self.org, "Grace Manager", 2)
        staffAssignment.objects.create(
            staff_member=self.clerk, branch=self.branch, staff_assignment="IC"
        )
        staffAssignment.objects.create(
            staff_member=self.manager, branch=self.branch, staff_assignment="AM"
        )

    def open_as(self, staff, username):
        self.sign_in_staff(staff, username)
        response = self.client.post(
            "/invt/stock-counts/open/",
            {"branch": self.branch.pk},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def test_a_line_is_signed_by_the_caller_not_by_the_body(self):
        count_id = self.open_as(self.clerk, "ken")
        response = self.client.post(
            f"/invt/stock-counts/{count_id}/record/",
            {
                "inventory": self.milk.pk,
                "counted": "48",
                "counted_by": self.manager.pk,
            },
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["counted_by_name"], "Ken Clerk")

        line = StockCount.objects.get(pk=count_id).lines.get()
        self.assertEqual(line.counted_by_staff_id, self.clerk.pk)

    def test_a_close_is_signed_by_the_caller_not_by_the_body(self):
        count_id = self.open_as(self.clerk, "ken")
        self.client.post(
            f"/invt/stock-counts/{count_id}/record/",
            {"inventory": self.milk.pk, "counted": "48"},
            content_type="application/json",
            **self.auth(),
        )

        # The clerk hands over; the manager signs it off. Two sessions, which
        # is the whole arrangement INVENTORY_COUNT_CLOSE exists to require.
        self.sign_in_staff(self.manager, "grace")
        response = self.client.post(
            f"/invt/stock-counts/{count_id}/close/",
            {"closed_by": self.clerk.pk},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 200, response.content)

        count = StockCount.objects.get(pk=count_id)
        self.assertEqual(count.closed_by_staff_id, self.manager.pk)
        self.assertEqual(count.opened_by_staff_id, self.clerk.pk)

    def test_the_clerk_who_counted_cannot_close_it(self):
        """
        The separation the two permissions were written for, asserted at the
        door rather than in the catalogue. `_INVENTORY` holds
        `inventory.count` and not `inventory.count.close`.
        """
        count_id = self.open_as(self.clerk, "ken")
        self.client.post(
            f"/invt/stock-counts/{count_id}/record/",
            {"inventory": self.milk.pk, "counted": "48"},
            content_type="application/json",
            **self.auth(),
        )
        response = self.client.post(
            f"/invt/stock-counts/{count_id}/close/",
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 403, response.content)

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.quantity, Decimal("50.00"))
