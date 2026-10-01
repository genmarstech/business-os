"""
Genmars' cross-tenant view of the platform.

The thing worth testing is not that the page renders. It is that the numbers
on it are the same numbers the rest of the application enforces against, and
that the one query behind it does not become fifty.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from inventory.models import BranchInventory
from organisations.admin import BusinessOrganizationAdmin
from organisations.models import BusinessOrganization, OrganizationStaff
from sales import services as sales_services
from sales.models import Payment


def a_shop(name, *, branches=1, tills=1, staff=1, inactive_staff=0):
    org = BusinessOrganization.objects.create(name=name)
    made = []
    for n in range(branches):
        branch = Branches.objects.create(
            organization=org, branch_name=f"{name} {n}", branch_location="Nairobi",
            branch_allocation="Ground", branch_manager="M", is_active=True,
        )
        made.append(branch)
        for t in range(tills):
            Register.objects.create(
                branch=branch, name=f"Till {t}", register_number=f"T{n}{t}"
            )

    people = []
    for n in range(staff + inactive_staff):
        person = OrganizationStaff.objects.create(
            organization=org, full_name=f"{name} person {n}",
            email=f"p{n}@{name.lower().replace(' ', '')}.co.ke",
            phone_number=f"+2547{abs(hash(name + str(n))) % 100000000:08d}",
            address="Nairobi", id_number=abs(hash(name + str(n))) % 1000000,
        )
        assignment = staffAssignment.objects.create(
            staff_member=person, branch=made[0], staff_assignment="CA"
        )
        if n >= staff:
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])
        people.append(person)
    return org, made, people


class RosterTests(TestCase):
    def setUp(self):
        self.admin = BusinessOrganizationAdmin(BusinessOrganization, AdminSite())
        self.request = RequestFactory().get("/admin/")
        self.request.user = User.objects.create_superuser(
            username="root", email="r@genmars.co.ke", password="x"
        )

    def row(self, org):
        return self.admin.get_queryset(self.request).get(pk=org.pk)

    def test_the_counts_are_what_they_say(self):
        org, _, _ = a_shop("Grocers", branches=3, tills=2, staff=4)
        row = self.row(org)
        self.assertEqual(self.admin.branches(row), 3)
        self.assertEqual(self.admin.tills(row), 6)
        self.assertEqual(self.admin.working_staff(row), 4)

    def test_several_joins_do_not_multiply_each_other(self):
        """
        ── WHY EVERY Count CARRIES distinct=True ──────────────────────────
        Three joins in one query produce the cross product of their rows.
        Without distinct, a shop with 3 branches, 6 tills and 4 staff
        reports 72 of each — numbers that look plausible enough on a
        dashboard that nobody checks them.
        """
        org, _, _ = a_shop("Chain", branches=3, tills=2, staff=4)
        row = self.row(org)
        self.assertEqual(
            (self.admin.branches(row), self.admin.tills(row),
             self.admin.working_staff(row)),
            (3, 6, 4),
        )

    def test_staff_means_people_working_not_rows_ever_written(self):
        """
        The same count `subscriptions.entitlement` enforces a plan limit
        against. If these two disagreed, this page would say a shop is
        under its limit while the server refused them for being over it.
        """
        org, _, _ = a_shop("Churn", staff=2, inactive_staff=5)
        self.assertEqual(self.admin.working_staff(self.row(org)), 2)

        from subscriptions import entitlement

        self.assertEqual(
            entitlement._in_use(org.pk, entitlement.STAFF),
            self.admin.working_staff(self.row(org)),
        )

    def test_a_shop_that_has_never_sold_says_so(self):
        org, _, _ = a_shop("Quiet")
        self.assertEqual(self.admin.last_traded(self.row(org)), "never")

    def test_last_traded_is_the_column_that_tells_you_something(self):
        org, (branch,), (person,) = a_shop("Busy")
        category = CatalogCategories.objects.create(organization=org, name="General")
        product = CatalogCategoryProduct.objects.create(
            organization=org, category=category, name="Milk", sku="SKU-1",
            cost_price=Decimal("70.00"), selling_price=Decimal("100.00"),
        )
        BranchInventory.objects.create(
            branch=branch, product=product, quantity=Decimal("50")
        )
        shift = RegisterShift.objects.create(
            register=branch.registers.first(), operator=person,
            opening_cash=Decimal("0.00"),
        )
        sale = sales_services.checkout(
            shift=shift, cashier=person,
            lines=[{"product": product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        self.assertEqual(self.admin.last_traded(self.row(org)), "today")

        sale.completed_at = timezone.now() - timedelta(days=9)
        sale.save(update_fields=["completed_at"])
        self.assertEqual(self.admin.last_traded(self.row(org)), "9 days ago")

    def test_a_tenant_with_no_subscription_reads_as_unrecorded(self):
        """
        Not as lapsed. Every organisation predating the subscriptions app
        has no row, and `entitlement.state_of` treats that as ACTIVE on
        purpose — this page must not contradict it.
        """
        org, _, _ = a_shop("Old")
        self.assertIn("unrecorded", self.admin.subscription(self.row(org)))

    def test_the_subscription_state_is_the_derived_one(self):
        from subscriptions import services as subscription_services

        org, _, _ = a_shop("New")
        subscription_services.open_trial(org)
        self.assertIn("On trial", self.admin.subscription(self.row(org)))

    def test_the_page_costs_a_fixed_number_of_queries(self):
        """
        Every column is an aggregate. Computed per row they would be five
        queries times fifty rows, on a page nobody would then open.
        """
        for n in range(12):
            a_shop(f"Shop {n}", branches=2, tills=2, staff=3)
        with self.assertNumQueries(1):
            list(self.admin.get_queryset(self.request))


class CrossTenantAccessTests(TestCase):
    """
    Who can reach the one view in the product that sees past a tenant.
    """

    def test_a_tenant_principal_is_not_a_django_user_at_all(self):
        """
        ══════════════════════════════════════════════════════════════════
        The structural reason /admin/ is safe to put a cross-tenant view
        behind: a PlatformAccount is not a Django user and a
        StaffCredential is not a Django user. There is no path from a
        subscriber or a cashier to this page, whatever permissions they
        accumulate inside their own tenant.
        ══════════════════════════════════════════════════════════════════
        """
        from identity.models import PlatformAccount, StaffCredential

        self.assertFalse(issubclass(PlatformAccount, User))
        self.assertFalse(issubclass(StaffCredential, User))

    def test_the_roster_refuses_an_anonymous_caller(self):
        response = self.client.get(
            reverse("admin:organisations_businessorganization_changelist")
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_a_tenant_cannot_be_added_or_deleted_from_here(self):
        """
        A business is created by its owner signing up, in a transaction that
        also makes them its owner and opens their trial. Deleting one takes
        a real shop's sales history with it.
        """
        admin = BusinessOrganizationAdmin(BusinessOrganization, AdminSite())
        self.assertFalse(admin.has_add_permission(None))
        self.assertFalse(admin.has_delete_permission(None))

    def test_the_shops_own_facts_are_not_editable_here(self):
        """
        Genmars can see a tenant's name and sector and cannot change them:
        those are the shop's facts, edited under Business details, and a
        support person quietly renaming somebody's business is a change with
        no trail on the side that cares.
        """
        admin = BusinessOrganizationAdmin(BusinessOrganization, AdminSite())
        for field in ("name", "org_number", "sector", "staff_size"):
            self.assertIn(field, admin.readonly_fields)
        # The one exception: who to invoice is Genmars' own fact.
        self.assertNotIn("genmars_organisation_id", admin.readonly_fields)
        self.assertIn("genmars_organisation_id", admin.fields)
