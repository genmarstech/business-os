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
from sales.models import Payment, Sale

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
        self.assertFalse(access.may(admin, access.SETTINGS_ORGANISATION))
        self.assertTrue(access.may(owner, access.STAFF_MANAGE))
        self.assertTrue(access.may(owner, access.SETTINGS_ORGANISATION))

        # But tax IS an admin's job — the settings split. A VAT rate change
        # is ordinary work and should not need the owner fetched.
        self.assertTrue(access.may(admin, access.SETTINGS_TAX))

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


class WhoAmITells(TestCase):
    """
    What a client is handed on boot, so it can draw a screen without offering
    buttons the server will refuse.

    The permission list is for DRAWING, never for guarding — every endpoint
    checks again. These tests are about it being accurate, because a list that
    disagrees with enforcement is worse than none: it either hides a button
    that works or offers one that does not.
    """

    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Shop A", branches=("Westlands", "Karen")
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

    def test_a_subscriber_is_told_what_they_may_do(self):
        self.as_subscriber(TenantMembership.Role.ACCOUNTANT, number=31)
        body = self.client.get("/auth/me").json()

        self.assertEqual(body["kind"], "subscriber")
        self.assertIn(access.SALES_VIEW, body["permissions"])
        self.assertNotIn(access.SALES_VOID, body["permissions"])

    def test_a_subscriber_has_no_branch_restriction(self):
        """
        null, not []. A client that reads null as "no branches" shows an owner
        an empty shop.
        """
        self.as_subscriber(TenantMembership.Role.OWNER, number=32)
        self.assertIsNone(self.client.get("/auth/me").json()["branches"])

    def test_what_it_reports_is_what_is_enforced(self):
        """
        The test that makes the list worth returning. It walks the reported
        permissions and checks the server agrees — a drift between these two
        is how a frontend comes to hide a working button.
        """
        self.as_subscriber(TenantMembership.Role.ADMIN, number=33)
        body = self.client.get("/auth/me").json()

        reported = set(body["permissions"])
        self.assertEqual(reported, set(access.SUBSCRIBER_ROLES["admin"]))

        # And the pair an admin must not hold is genuinely absent from both.
        self.assertNotIn(access.STAFF_MANAGE, reported)
        self.assertEqual(self.client.get("/brn/staff-assignments/").status_code, 403)

    def test_an_admin_may_configure_tax_and_not_rename_the_business(self):
        """
        The settings split. A VAT rate change is ordinary work; the
        organisation's registered identity is the owner's.
        """
        self.as_subscriber(TenantMembership.Role.ADMIN, number=34)
        body = self.client.get("/auth/me").json()

        self.assertIn(access.SETTINGS_TAX, body["permissions"])
        self.assertNotIn(access.SETTINGS_ORGANISATION, body["permissions"])

        made = self.client.post(
            "/sls/tax-rules/",
            {
                "organization": self.org.pk,
                "name": "VAT 16%",
                "rate": "16.00",
                "is_inclusive": True,
                "is_default": True,
            },
            content_type="application/json",
        )
        self.assertEqual(made.status_code, 201, made.content)

        renamed = self.client.patch(
            f"/org/organizations/{self.org.pk}/",
            {"name": "Something Else Entirely"},
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 403)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "Shop A")

    def test_an_owner_may_rename_the_business(self):
        """The control for the refusal above."""
        self.as_subscriber(TenantMembership.Role.OWNER, number=35)
        renamed = self.client.patch(
            f"/org/organizations/{self.org.pk}/",
            {"name": "Shop A Holdings"},
            content_type="application/json",
        )
        self.assertEqual(renamed.status_code, 200, renamed.content)

    def test_a_till_is_told_its_permissions_per_branch(self):
        """
        A cashier at Westlands and manager at Karen must not be shown a Refund
        button at Westlands. The union says "somewhere, yes"; the per-branch
        map is what a screen should actually draw from.
        """
        jane = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        assign(jane, self.westlands, staffAssignment.StaffRoles.Cashier)
        assign(jane, self.karen, staffAssignment.StaffRoles.AssistantManager)
        credential = a_till(self.org, jane, "jane")
        _, token = services.open_staff_session(credential)

        body = self.client.get(
            "/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}"
        ).json()

        self.assertEqual(body["kind"], "staff")
        self.assertEqual(
            sorted(body["branches"]), sorted([self.westlands.pk, self.karen.pk])
        )

        at_westlands = body["permissions_by_branch"][str(self.westlands.pk)]
        at_karen = body["permissions_by_branch"][str(self.karen.pk)]

        self.assertNotIn(access.SALES_REFUND, at_westlands)
        self.assertIn(access.SALES_REFUND, at_karen)
        # The union still says yes, which is why a screen must not use it.
        self.assertIn(access.SALES_REFUND, body["permissions"])


class CsrfTests(TestCase):
    """
    A cookie-authenticated write must carry a CSRF token.

    ── WHY THIS IS NOT COVERED BY THE REST OF THE SUITE ───────────────────
    Django's test client sets `_dont_enforce_csrf_checks`, so every other test
    here passes whether the check exists or not. These use
    Client(enforce_csrf_checks=True), which is the only way to see the real
    behaviour — and the reason the gap survived unnoticed: the authentication
    class subclasses BaseAuthentication, and DRF enforces CSRF only inside
    SessionAuthentication.
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")
        self.account = PlatformAccount.objects.create(
            genmars_account_id=41, email="owner@a.co.ke", full_name="A Owner"
        )
        TenantMembership.objects.create(
            account=self.account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )

    def strict(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        session = client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()
        return client

    def a_branch(self):
        return {
            # `organization_id` — BranchesSerializer nests the organisation for
            # reading and takes the id for writing.
            "organization_id": self.org.pk,
            "branch_name": "Karen",
            "branch_location": "Nairobi",
            "branch_allocation": "Ground floor",
            "branch_manager": "A Manager",
        }

    def test_a_write_without_a_token_is_refused(self):
        response = self.strict().post(
            "/brn/branch/", self.a_branch(), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("CSRF", response.json()["detail"])
        self.assertEqual(Branches.objects.filter(branch_name="Karen").count(), 0)

    def test_the_same_write_with_a_token_succeeds(self):
        """
        The positive control. Without it the refusal above would pass just as
        happily if branch creation were simply broken.
        """
        client = self.strict()

        # ── THE TOKEN COMES FROM /auth/me ──────────────────────────────
        # Django writes the cookie only when a request asks for a token, and
        # nothing in a JSON API does that by itself. WhoAmIView carries
        # @ensure_csrf_cookie for exactly this reason — without it a client
        # can never obtain a token and every write is refused.
        client.get("/auth/me")
        token = client.cookies["csrftoken"].value

        response = client.post(
            "/brn/branch/",
            self.a_branch(),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Branches.objects.filter(branch_name="Karen").count(), 1)

    def test_auth_me_is_what_issues_the_cookie(self):
        """
        Pinned because it is a side effect. Somebody tidying the decorator off
        WhoAmIView would break every write in the application, and nothing
        else in the suite would notice.
        """
        client = self.strict()
        self.assertNotIn("csrftoken", client.cookies)
        client.get("/auth/me")
        self.assertIn("csrftoken", client.cookies)

    def test_reading_never_needs_a_token(self):
        """
        CsrfViewMiddleware exempts the safe methods itself. If a GET ever
        started demanding a token the whole application would stop loading,
        so it is worth pinning.
        """
        self.assertEqual(self.strict().get("/brn/branch/").status_code, 200)

    def test_a_till_is_not_asked_for_one(self):
        """
        ⚠ A bearer token is not sent automatically by a browser, so there is
        nothing to forge. Enforcing CSRF on the staff class would break every
        till for no gain — and a till has no cookie to read a token from.
        """
        from django.test import Client

        jane = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        assign(jane, self.branch, staffAssignment.StaffRoles.Cashier)
        credential = a_till(self.org, jane, "jane")
        _, token = services.open_staff_session(credential)

        response = Client(enforce_csrf_checks=True).get(
            "/auth/me", HTTP_AUTHORIZATION=f"Bearer {token}"
        )
        self.assertEqual(response.status_code, 200)


class BrandNewSubscriberTests(TestCase):
    """
    Somebody who has just signed in through Genmars and has no business yet.

    ── THE STATE EVERY OTHER TEST SKIPS ───────────────────────────────────
    Every test in this file creates a TenantMembership in setUp, because that
    is what makes the interesting assertions possible. So the one state a real
    person is guaranteed to pass through — signed in, belonging to nothing —
    was covered nowhere, and onboarding shipped impossible: /auth/me was gated
    on membership, so a new subscriber could not learn they had no business,
    could not obtain the CSRF cookie it issues, and therefore could not create
    one either.
    """

    def setUp(self):
        self.account = PlatformAccount.objects.create(
            genmars_account_id=51, email="brand.new@owner.co.ke", full_name="New"
        )

    def client_for(self, **kwargs):
        from django.test import Client

        client = Client(**kwargs)
        session = client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()
        return client

    def test_they_can_ask_who_they_are(self):
        response = self.client_for().get("/auth/me")
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body["kind"], "subscriber")
        self.assertEqual(body["organisations"], [])
        self.assertEqual(body["permissions"], [])
        # Not restricted to nothing — they simply have no tenant at all.
        self.assertIsNone(body["branches"])

    def test_they_can_obtain_a_csrf_token(self):
        """
        Being refused /auth/me also meant being refused the cookie it issues,
        so the deadlock closed both ways: no token, therefore no write, ever.
        """
        client = self.client_for(enforce_csrf_checks=True)
        client.get("/auth/me")
        self.assertIn("csrftoken", client.cookies)

    def test_they_can_create_their_first_business(self):
        """The whole point. This is the write that ends the state."""
        client = self.client_for(enforce_csrf_checks=True)
        client.get("/auth/me")

        response = client.post(
            "/org/organizations/",
            {"name": "Brand New Stores", "staff_size": "MD"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 201, response.content)

        # And it made them its owner, so the next request is no longer this
        # state at all.
        membership = TenantMembership.objects.get(account=self.account)
        self.assertEqual(membership.role, TenantMembership.Role.OWNER)

        after = client.get("/auth/me").json()
        self.assertEqual(len(after["organisations"]), 1)
        self.assertIn(access.SETTINGS_ORGANISATION, after["permissions"])

    def test_they_still_reach_nothing_that_holds_tenant_data(self):
        """
        Widening /auth/me must not have widened anything else. They belong to
        no tenant, so every real endpoint is still shut.
        """
        client = self.client_for()
        for path in ("/brn/branch/", "/ctl/products/", "/sls/sales/", "/org/staff/"):
            self.assertEqual(
                client.get(path).status_code, 403, f"{path} was reachable"
            )


class AttributionTests(TestCase):
    """
    A till records the sale against whoever is signed in to it.

    ══════════════════════════════════════════════════════════════════════════
    THE HOLE THIS CLOSES, AND WHY THE SCOPE CHECK DID NOT.

    `cashier` and `processed_by` arrive from the client, and the tenant check
    only asks whether that person works for the same shop. Every colleague
    does. So one cashier could put their own takings — or a refund they gave
    themselves — under somebody else's name, and `Sale.cashier` is exactly the
    field a drawer is reconciled against and a disputed transaction traced
    through. An attribution that can be set to anyone is not one.

    Found while wiring the till, which needed its own staff id to open a shift
    at all and could therefore just as easily have sent a different one.
    ══════════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop("Shop A")

        self.jane = a_staff(
            self.org, name="Jane", email="jane@a.co.ke", id_number=2001
        )
        self.john = a_staff(
            self.org, name="John", email="john@a.co.ke", id_number=2002
        )
        assign(self.jane, self.branch, staffAssignment.StaffRoles.Cashier)
        assign(self.john, self.branch, staffAssignment.StaffRoles.Cashier)

        credential = a_till(self.org, self.jane, "jane")
        _, self.token = services.open_staff_session(credential)

        self.register = Register.objects.create(
            branch=self.branch, name="Till 1", register_number="T1"
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
            branch=self.branch, product=self.product, quantity=Decimal("50")
        )

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def sale(self, cashier_id, **extra):
        return self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": self.shift.pk,
                "cashier": cashier_id,
                "lines": [{"product": self.product.pk, "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "100.00"}],
                **extra,
            },
            content_type="application/json",
            **self.auth(),
        )

    def test_a_till_may_ring_up_as_itself(self):
        """The control. Without it the refusal below proves only a broken till."""
        response = self.sale(self.jane.pk)
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_till_may_not_ring_up_as_a_colleague(self):
        response = self.sale(self.john.pk)
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(Sale.objects.count(), 0)

    def test_an_owner_may_still_record_a_sale_for_somebody(self):
        """
        The rule is about the tier that does not hold organisation-wide
        authority. An owner entering a sale on a cashier's behalf is ordinary
        and remains allowed — otherwise this would have broken every back
        -office correction.
        """
        account = PlatformAccount.objects.create(
            genmars_account_id=4242, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

        response = self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": self.shift.pk,
                "cashier": self.john.pk,
                "lines": [{"product": self.product.pk, "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "100.00"}],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def test_a_till_learns_its_own_staff_id_and_only_its_own(self):
        """
        It has to: /org/staff/ is held at staff.manage, which no cashier
        holds, so without this the register could sign in and never open a
        shift. The id confers nothing — the test above is what makes that
        true rather than hopeful.
        """
        me = self.client.get("/auth/me", **self.auth()).json()
        self.assertEqual(me["staff_id"], self.jane.pk)
        self.assertEqual(
            self.client.get("/org/staff/", **self.auth()).status_code, 403
        )
