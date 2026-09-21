"""
Roles and permissions — blueprint §8.

The tests that matter here are the NEGATIVE ones. A permission system is easy
to write so that everybody can do everything, and every positive test still
passes; what proves it works is a cashier being refused the things a cashier
should not have, with a positive control beside it so "refused" cannot quietly
mean "the endpoint is broken for everybody".
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity import access, services
from identity.authentication import SUBSCRIBER_SESSION_KEY, StaffPrincipal
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization, OrganizationStaff
from sales.models import Payment

PASSWORD = "till-password-not-real"


def a_shop(name, *, branches=("Main",)):
    org = BusinessOrganization.objects.create(name=name)
    made = []
    for index, branch_name in enumerate(branches):
        made.append(
            Branches.objects.create(
                organization=org,
                branch_name=f"{name} {branch_name}",
                branch_location="Nairobi",
                branch_allocation="Ground floor",
                branch_manager="A Manager",
                is_active=True,
            )
        )
    return org, made


def a_staff(org, *, name, email, id_number):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=email,
        # Unique per organisation, so derive it — two staff sharing a number
        # trips the constraint and the failure looks nothing like its cause.
        phone_number=f"+2547{id_number:08d}",
        address="Nairobi",
        id_number=id_number,
    )


def assign(staff, branch, role):
    return staffAssignment.objects.create(
        staff_member=staff, branch=branch, staff_assignment=role
    )


def a_till(org, staff, username):
    credential = StaffCredential(staff=staff, username=username)
    credential.set_password(PASSWORD)
    credential.save()
    return credential


class CatalogueTests(TestCase):
    """The map itself, before anything uses it."""

    def test_every_role_grants_only_known_permissions(self):
        """
        The assertion at the bottom of access.py, restated as a test so it
        shows up in a run rather than only at import.
        """
        for role, grants in {
            **access.SUBSCRIBER_ROLES,
            **access.OPERATIONAL_ROLES,
        }.items():
            self.assertTrue(
                set(grants) <= access.KNOWN,
                f"{role} grants something not in the catalogue",
            )

    def test_an_owner_holds_everything(self):
        self.assertEqual(
            access.SUBSCRIBER_ROLES[TenantMembership.Role.OWNER], access.KNOWN
        )

    def test_an_unknown_permission_is_refused_rather_than_allowed(self):
        """
        A typo in a view is a string that matches nothing. It has to fail
        closed, because the alternative is a view that silently checks
        nothing at all.
        """
        account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@a.co.ke"
        )
        org, _ = a_shop("Shop A")
        TenantMembership.objects.create(
            account=account, organization=org, role=TenantMembership.Role.OWNER
        )
        self.assertTrue(access.may(account, access.SALES_VOID))
        self.assertFalse(access.may(account, "sales.definitely-not-a-permission"))


class SubscriberRoleTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")

    def account(self, role, *, number):
        account = PlatformAccount.objects.create(
            genmars_account_id=number, email=f"{role}@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account, organization=self.org, role=role
        )
        return account

    def test_an_accountant_reads_the_money_and_touches_none_of_it(self):
        """
        An accountant who could void a sale could make a discrepancy
        disappear instead of explaining it, which is the one thing the role
        exists to prevent.
        """
        accountant = self.account(TenantMembership.Role.ACCOUNTANT, number=11)

        self.assertTrue(access.may(accountant, access.SALES_VIEW))
        self.assertTrue(access.may(accountant, access.REPORTS_ORGANISATION))

        self.assertFalse(access.may(accountant, access.SALES_VOID))
        self.assertFalse(access.may(accountant, access.SALES_REFUND))
        self.assertFalse(access.may(accountant, access.SALES_CHECKOUT))
        self.assertFalse(access.may(accountant, access.CATALOG_MANAGE))

    def test_an_admin_cannot_change_who_administers_the_business(self):
        """
        The permission that grants every other permission is the narrow one —
        the same instinct as gen-portal reserving can_manage_access.
        """
        admin = self.account(TenantMembership.Role.ADMIN, number=12)
        owner = self.account(TenantMembership.Role.OWNER, number=13)

        self.assertFalse(access.may(admin, access.STAFF_MANAGE))
        self.assertFalse(access.may(admin, access.SETTINGS_MANAGE))
        self.assertTrue(access.may(owner, access.STAFF_MANAGE))
        self.assertTrue(access.may(owner, access.SETTINGS_MANAGE))

        # The control: an admin is not simply powerless.
        self.assertTrue(access.may(admin, access.SALES_REFUND))
        self.assertTrue(access.may(admin, access.CATALOG_MANAGE))

    def test_a_subscriber_is_not_confined_to_branches(self):
        """
        None, not []. Organisation-wide authority is "unrestricted", and an
        empty list would mean the opposite.
        """
        owner = self.account(TenantMembership.Role.OWNER, number=14)
        self.assertIsNone(access.branch_scope(owner))


class OperationalRoleTests(TestCase):
    """A real till principal, with assignments."""

    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Shop A", branches=("Westlands", "Karen")
        )
        self.jane = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        self.credential = a_till(self.org, self.jane, "jane")
        assign(self.jane, self.westlands, staffAssignment.StaffRoles.Cashier)

        _, self.token = services.open_staff_session(self.credential)
        self.principal = StaffPrincipal(services.resolve_staff_session(self.token))

    def test_a_cashier_may_sell_and_may_not_undo_a_sale(self):
        """
        Blueprint module 6 lists "manager approvals" beside cashier access.
        Voiding and refunding are how a till is emptied by the person standing
        at it; the cheapest second person is a permission the first lacks.
        """
        self.assertTrue(access.may(self.principal, access.SALES_CHECKOUT))
        self.assertTrue(access.may(self.principal, access.SALES_VIEW))
        self.assertTrue(access.may(self.principal, access.SHIFT_OPEN))

        self.assertFalse(access.may(self.principal, access.SALES_VOID))
        self.assertFalse(access.may(self.principal, access.SALES_REFUND))
        self.assertFalse(access.may(self.principal, access.SHIFT_CLOSE))
        self.assertFalse(access.may(self.principal, access.REPORTS_BRANCH))
        self.assertFalse(access.may(self.principal, access.CATALOG_MANAGE))

    def test_a_cashier_is_confined_to_the_branch_they_work_at(self):
        self.assertEqual(access.branch_scope(self.principal), [self.westlands.pk])

    def test_two_assignments_do_not_merge_into_one_bigger_role(self):
        """
        ── THE ESCALATION THIS IS ALL FOR ──────────────────────────────────
        Jane is a cashier at Westlands and the manager at Karen. Taking the
        union of her roles and applying it everywhere would make her a manager
        at Westlands too — authority nobody granted, arrived at by adding two
        ordinary assignments.
        """
        assign(self.jane, self.karen, staffAssignment.StaffRoles.AssistantManager)
        principal = StaffPrincipal(services.resolve_staff_session(self.token))

        # At Karen she may refund. At Westlands she may not.
        self.assertTrue(
            access.may(principal, access.SALES_REFUND, self.karen.pk)
        )
        self.assertFalse(
            access.may(principal, access.SALES_REFUND, self.westlands.pk)
        )

        # Asked without a branch, the union answers "somewhere, yes" — which
        # is the right answer for reaching the endpoint and the wrong one for
        # acting, which is why the views pass the branch once they know it.
        self.assertTrue(access.may(principal, access.SALES_REFUND))

        self.assertEqual(
            sorted(access.branch_scope(principal)),
            sorted([self.westlands.pk, self.karen.pk]),
        )

    def test_taking_somebody_off_the_rota_ends_their_access(self):
        """
        An empty branch scope is confined to NOTHING, which is the correct
        answer for a staff member whose assignments have been deactivated
        while they still hold a live session.
        """
        staffAssignment.objects.filter(staff_member=self.jane).update(
            is_active=False
        )
        principal = StaffPrincipal(services.resolve_staff_session(self.token))

        self.assertEqual(access.branch_scope(principal), [])
        self.assertFalse(access.may(principal, access.SALES_CHECKOUT))

    def test_an_inventory_clerk_cannot_operate_a_till(self):
        clerk = a_staff(
            self.org, name="Ken Clerk", email="ken@a.co.ke", id_number=1002
        )
        assign(clerk, self.westlands, staffAssignment.StaffRoles.InventoryClerk)
        credential = a_till(self.org, clerk, "ken")
        _, token = services.open_staff_session(credential)
        principal = StaffPrincipal(services.resolve_staff_session(token))

        self.assertTrue(access.may(principal, access.INVENTORY_ADJUST))
        self.assertFalse(access.may(principal, access.SALES_CHECKOUT))

    def test_an_auditor_reads_and_writes_nothing(self):
        auditor = a_staff(
            self.org, name="Ada Auditor", email="ada@a.co.ke", id_number=1003
        )
        assign(auditor, self.westlands, staffAssignment.StaffRoles.BranchAuditor)
        credential = a_till(self.org, auditor, "ada")
        _, token = services.open_staff_session(credential)
        principal = StaffPrincipal(services.resolve_staff_session(token))

        held = access.granted(principal)
        self.assertTrue(access.SALES_VIEW in held)
        for write in (
            access.SALES_CHECKOUT, access.SALES_VOID, access.SALES_REFUND,
            access.INVENTORY_ADJUST, access.CATALOG_MANAGE,
            access.CUSTOMER_MANAGE, access.STAFF_MANAGE,
        ):
            self.assertNotIn(write, held, f"an auditor should not hold {write}")


class PermissionOverHttpTests(TestCase):
    """
    The map is only worth as much as its enforcement. These go through the
    HTTP surface, which is where a real caller stands.
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")
        category = CatalogCategories.objects.create(
            organization=self.org, name="General"
        )
        self.product = CatalogCategoryProduct.objects.create(
            organization=self.org,
            category=category,
            name="Milk",
            sku="SKU-1",
            cost_price=Decimal("70.00"),
            selling_price=Decimal("100.00"),
        )
        BranchInventory.objects.create(
            branch=self.branch, product=self.product, quantity=Decimal("50")
        )
        self.register = Register.objects.create(
            branch=self.branch, name="Till 1", register_number="T1"
        )

    def as_subscriber(self, role, *, number):
        account = PlatformAccount.objects.create(
            genmars_account_id=number, email=f"{role}-{number}@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account, organization=self.org, role=role
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()
        return account

    def test_an_accountant_may_read_sales_and_may_not_edit_the_catalogue(self):
        self.as_subscriber(TenantMembership.Role.ACCOUNTANT, number=21)

        self.assertEqual(self.client.get("/sls/sales/").status_code, 200)
        self.assertEqual(self.client.get("/ctl/products/").status_code, 200)

        refused = self.client.post(
            "/ctl/products/",
            {
                "organization": self.org.pk,
                "category": self.product.category_id,
                "name": "Bread",
                "sku": "SKU-2",
                "cost_price": "40.00",
                "selling_price": "60.00",
            },
            content_type="application/json",
        )
        self.assertEqual(refused.status_code, 403)

    def test_an_admin_may_edit_the_catalogue(self):
        """
        The positive control for the test above. Without it a 403 caused by a
        broken serialiser would read as a working permission system.
        """
        self.as_subscriber(TenantMembership.Role.ADMIN, number=22)
        made = self.client.post(
            "/ctl/products/",
            {
                "organization": self.org.pk,
                "category": self.product.category_id,
                "name": "Bread",
                "sku": "SKU-2",
                "cost_price": "40.00",
                "selling_price": "60.00",
            },
            content_type="application/json",
        )
        self.assertEqual(made.status_code, 201, made.content)

    def test_an_admin_may_not_reach_the_staff_table(self):
        """
        staffAssignment is the table that grants every operational permission
        in access.py, so writing to it is held at the narrowest permission
        there is.
        """
        self.as_subscriber(TenantMembership.Role.ADMIN, number=23)
        self.assertEqual(self.client.get("/brn/staff-assignments/").status_code, 403)

    def test_an_owner_may(self):
        self.as_subscriber(TenantMembership.Role.OWNER, number=24)
        self.assertEqual(self.client.get("/brn/staff-assignments/").status_code, 200)

    def test_an_accountant_gets_the_consolidated_report(self):
        self.as_subscriber(TenantMembership.Role.ACCOUNTANT, number=25)
        self.assertEqual(
            self.client.get("/sls/reports/overview/").status_code, 200
        )


class TillOverHttpTests(TestCase):
    """A real operational principal, over HTTP, with a bearer token."""

    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Shop A", branches=("Westlands", "Karen")
        )
        self.jane = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        assign(self.jane, self.westlands, staffAssignment.StaffRoles.Cashier)
        credential = a_till(self.org, self.jane, "jane")
        _, self.token = services.open_staff_session(credential)

        self.register = Register.objects.create(
            branch=self.westlands, name="Till 1", register_number="T1"
        )
        self.shift = RegisterShift.objects.create(
            register=self.register, operator=self.jane,
            opening_cash=Decimal("1000.00"),
        )

        category = CatalogCategories.objects.create(
            organization=self.org, name="General"
        )
        self.product = CatalogCategoryProduct.objects.create(
            organization=self.org, category=category, name="Milk", sku="SKU-1",
            cost_price=Decimal("70.00"), selling_price=Decimal("100.00"),
        )
        BranchInventory.objects.create(
            branch=self.westlands, product=self.product, quantity=Decimal("50")
        )

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def test_a_cashier_can_check_out(self):
        """The control. Everything below is a refusal and needs this first."""
        response = self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": self.shift.pk,
                "cashier": self.jane.pk,
                "lines": [{"product": self.product.pk, "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "100.00"}],
            },
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_cashier_cannot_read_the_branch_report(self):
        response = self.client.get("/sls/reports/overview/", **self.auth())
        self.assertEqual(response.status_code, 403)

    def test_a_cashier_cannot_change_a_price(self):
        response = self.client.patch(
            f"/ctl/products/{self.product.pk}/",
            {"selling_price": "1.00"},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.selling_price, Decimal("100.00"))

    def test_a_cashier_cannot_void_a_sale(self):
        from sales import services as sales_services

        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.jane,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        response = self.client.post(
            f"/sls/sales/{sale.pk}/void/",
            {"reason": "Because I can"},
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 403)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "completed")

    def test_a_cashier_does_not_see_another_branchs_sales(self):
        """
        §8, the part that was missing before roles: tenant scoping alone left
        a cashier at Westlands reading Karen's takings.
        """
        from sales import services as sales_services

        karen_register = Register.objects.create(
            branch=self.karen, name="Till 1", register_number="T1"
        )
        karen_staff = a_staff(
            self.org, name="Ken Karen", email="ken@a.co.ke", id_number=1009
        )
        karen_shift = RegisterShift.objects.create(
            register=karen_register, operator=karen_staff,
            opening_cash=Decimal("500.00"),
        )
        BranchInventory.objects.create(
            branch=self.karen, product=self.product, quantity=Decimal("50")
        )
        karen_sale = sales_services.checkout(
            shift=karen_shift,
            cashier=karen_staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )

        listing = self.client.get("/sls/sales/", **self.auth())
        self.assertEqual(listing.status_code, 200)
        body = listing.json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertEqual(rows, [])

        # And not by id either — 404, not 403, because a 403 confirms it
        # exists.
        one = self.client.get(f"/sls/sales/{karen_sale.pk}/", **self.auth())
        self.assertEqual(one.status_code, 404)

    def test_a_cashier_cannot_check_out_at_a_branch_they_are_not_assigned_to(self):
        karen_register = Register.objects.create(
            branch=self.karen, name="Till 1", register_number="T1"
        )
        karen_shift = RegisterShift.objects.create(
            register=karen_register, operator=self.jane,
            opening_cash=Decimal("500.00"),
        )
        BranchInventory.objects.create(
            branch=self.karen, product=self.product, quantity=Decimal("50")
        )

        response = self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": karen_shift.pk,
                "cashier": self.jane.pk,
                "lines": [{"product": self.product.pk, "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "100.00"}],
            },
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 403, response.content)


class EveryViewsetIsGatedTests(TestCase):
    """
    ── THE FAILURE MODE THIS WHOLE FILE EXISTS FOR ─────────────────────────
    A permission system does not break loudly. It breaks by somebody adding a
    viewset next quarter, wiring it into the router, and never declaring a
    permission — at which point it is tenant-scoped and otherwise wide open,
    every test passes, and nothing says so.

    So this walks the live URL configuration rather than a hand-kept list. A
    new endpoint is in scope the moment it is routed.
    """

    def viewsets(self):
        from django.urls import get_resolver

        from identity.scoping import TenantScoped

        found = {}

        def walk(patterns, prefix=""):
            for entry in patterns:
                if hasattr(entry, "url_patterns"):
                    walk(entry.url_patterns, prefix + str(entry.pattern))
                    continue
                callback = getattr(entry, "callback", None)
                cls = getattr(callback, "cls", None)
                if cls is not None and issubclass(cls, TenantScoped):
                    found[cls.__name__] = cls

        walk(get_resolver().url_patterns)
        return found

    def test_the_walk_finds_something(self):
        """
        The control. If the resolver walk silently found nothing, every
        assertion below would pass over an empty dictionary.
        """
        self.assertGreaterEqual(len(self.viewsets()), 10)

    def test_every_routed_viewset_declares_a_permission(self):
        ungated = []
        for name, cls in self.viewsets().items():
            if cls.default_permission is None and not cls.permissions:
                ungated.append(name)
        self.assertEqual(
            ungated,
            [],
            "these are routed and tenant-scoped but gate nothing: "
            f"{ungated}. Declare `permissions` or `default_permission`, or "
            "say in a comment why the endpoint is open to any member.",
        )

    def test_every_declared_permission_is_in_the_catalogue(self):
        """
        A permission name that is not in KNOWN fails closed — `may()` refuses
        it — so a typo locks somebody out of their own till rather than
        opening a door. Catching it here means catching it in CI instead.
        """
        for name, cls in self.viewsets().items():
            declared = set(cls.permissions.values())
            if cls.default_permission:
                declared.add(cls.default_permission)
            unknown = declared - access.KNOWN
            self.assertEqual(
                unknown, set(), f"{name} names permissions that do not exist: {unknown}"
            )

    def test_anything_belonging_to_a_branch_is_scoped_to_one(self):
        """
        `branch_path` is the other half, and it fails open exactly the same
        way: a model that reaches a branch but does not declare the path is a
        model a cashier reads across every branch in the shop.

        The check is structural — does the model have a route to a branch —
        rather than a list somebody has to remember to update.
        """
        missing = []
        for name, cls in self.viewsets().items():
            if cls.branch_path:
                continue
            model = cls.queryset.model
            # CONCRETE fields only. Reverse relations would flag Branches
            # itself, which does not belong to a branch — it is one.
            fields = {
                f.name
                for f in model._meta.get_fields()
                if getattr(f, "concrete", False)
            }
            reaches_a_branch = bool(
                fields & {"branch", "from_branch", "register", "inventory"}
            )
            if reaches_a_branch:
                missing.append(name)
        self.assertEqual(
            missing,
            [],
            f"these reach a branch but are not scoped to one: {missing}",
        )
