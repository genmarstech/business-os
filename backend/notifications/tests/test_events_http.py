"""
The other three event families, and the endpoints, through HTTP.

Plus the one property the whole design rests on: a broken notification must not
be able to fail a sale.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from django.test import TestCase

from identity import access
from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory.models import BranchInventory
from notifications.models import Notification
from organisations.models import BusinessOrganization, OrganizationStaff


def a_shop(name):
    org = BusinessOrganization.objects.create(name=name)
    branch = Branches.objects.create(
        organization=org,
        branch_name=f"{name} Main",
        branch_location="Nairobi",
        branch_allocation="Ground floor",
        branch_manager="A Manager",
        is_active=True,
    )
    return org, branch


def a_person(org, name, n):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=f"p{n}@example.co.ke",
        phone_number=f"+2547{n:08d}",
        address="Nairobi",
        id_number=n,
    )


def a_product(org, *, name, sku):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
    return CatalogCategoryProduct.objects.create(
        organization=org, category=category, name=name, sku=sku,
        cost_price=Decimal("70.00"), selling_price=Decimal("100.00"),
    )


class Base(TestCase):
    def setUp(self):
        self.org, self.branch = a_shop("Kilimani Dental")
        self.owner = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop.co.ke", full_name="Asha Owner"
        )
        TenantMembership.objects.create(
            account=self.owner, organization=self.org,
            role=TenantMembership.Role.OWNER,
        )

    def as_owner(self):
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.owner.pk
        session.save()

    def live(self, kind=None):
        rows = Notification.objects.filter(resolved_at__isnull=True)
        return rows.filter(kind=kind) if kind else rows


class ShiftClosingTests(Base):
    def a_shift(self, *, opening="0"):
        person = a_person(self.org, "Jane Cashier", 10000001)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment="CA"
        )
        register = Register.objects.create(
            branch=self.branch, name="Till 1", is_active=True
        )
        return RegisterShift.objects.create(
            register=register, operator=person,
            opening_cash=Decimal(opening), status="OPEN",
        )

    def test_a_drawer_that_agrees_is_worth_knowing(self):
        from branches import services as branch_services

        shift = self.a_shift(opening="500")
        branch_services.close_shift(shift=shift, counted_cash=Decimal("500"))

        row = self.live(Notification.Kind.SHIFT_CLOSED).get()
        self.assertEqual(row.urgency, Notification.Urgency.INFORM)
        self.assertIn("agreed", row.subject)

    def test_a_short_drawer_is_its_own_notification(self):
        """
        The thing a manager wants on the day rather than in a month-end report,
        by which time nobody remembers who was on the register.
        """
        from branches import services as branch_services

        shift = self.a_shift(opening="500")
        branch_services.close_shift(shift=shift, counted_cash=Decimal("200"))

        row = self.live(Notification.Kind.SHIFT_SHORT).get()
        self.assertEqual(row.urgency, Notification.Urgency.ATTEND)
        self.assertIn("short", row.subject)
        self.assertEqual(row.permission, access.REPORTS_BRANCH)

    def test_an_over_drawer_is_reported_as_shorts_opposite(self):
        """
        More in the drawer than there should be is an unrecorded sale, a wrong
        float, or change given wrongly. All three are worth a question.
        """
        from branches import services as branch_services

        shift = self.a_shift(opening="500")
        branch_services.close_shift(shift=shift, counted_cash=Decimal("800"))

        row = self.live(Notification.Kind.SHIFT_SHORT).get()
        self.assertIn("over", row.subject)


class CountSignOffTests(Base):
    def setUp(self):
        super().setUp()
        self.product = a_product(self.org, name="Blue Band", sku="BB")
        self.inventory = BranchInventory.objects.create(
            branch=self.branch, product=self.product,
            quantity=Decimal("10"), reorder_level=Decimal("0"),
        )

    def test_counting_the_last_shelf_row_asks_for_a_sign_off(self):
        from inventory import services as inventory_services

        count = inventory_services.open_count(branch=self.branch, actor=self.owner)
        self.assertEqual(self.live(Notification.Kind.COUNT_AWAITING).count(), 0)

        inventory_services.record_count(
            count=count, inventory=self.inventory, counted=Decimal("9"),
            actor=self.owner,
        )

        row = self.live(Notification.Kind.COUNT_AWAITING).get()
        self.assertEqual(row.permission, access.INVENTORY_COUNT_CLOSE)

    def test_it_says_nothing_about_what_the_count_found(self):
        """
        ══════════════════════════════════════════════════════════════════════
        THE COUNT IS BLIND, AND A NOTIFICATION IS A PLACE THAT COULD LEAK IT.

        The person counting must not learn the expected figure — show it and
        they count to it, and the stock take stops detecting anything. A
        notification saying "4 short" sits in the same feed, so the variance is
        absent on purpose: this says it is ready, and nothing about what it
        says.
        ══════════════════════════════════════════════════════════════════════
        """
        from inventory import services as inventory_services

        count = inventory_services.open_count(branch=self.branch, actor=self.owner)
        inventory_services.record_count(
            count=count, inventory=self.inventory, counted=Decimal("4"),
            actor=self.owner,
        )

        row = self.live(Notification.Kind.COUNT_AWAITING).get()
        words = f"{row.subject} {row.body}"
        # The expected figure was 10 and the counted was 4, so a variance of 6.
        for leak in ("10", "6", "short", "over", "variance"):
            self.assertNotIn(leak, words, f"the notification leaked {leak!r}")

    def test_correcting_a_figure_does_not_ask_twice(self):
        from inventory import services as inventory_services

        count = inventory_services.open_count(branch=self.branch, actor=self.owner)
        for figure in ("9", "8", "7"):
            inventory_services.record_count(
                count=count, inventory=self.inventory,
                counted=Decimal(figure), actor=self.owner,
            )
        self.assertEqual(self.live(Notification.Kind.COUNT_AWAITING).count(), 1)

    def test_signing_off_resolves_it(self):
        from inventory import services as inventory_services

        count = inventory_services.open_count(branch=self.branch, actor=self.owner)
        inventory_services.record_count(
            count=count, inventory=self.inventory, counted=Decimal("9"),
            actor=self.owner,
        )
        inventory_services.close_count(count=count, actor=self.owner)

        self.assertEqual(self.live(Notification.Kind.COUNT_AWAITING).count(), 0)

    def test_abandoning_resolves_it_too(self):
        """
        The path that would have been forgotten: signing off is the happy one,
        so an abandoned count would have sat in somebody's list for ever.
        """
        from inventory import services as inventory_services

        count = inventory_services.open_count(branch=self.branch, actor=self.owner)
        inventory_services.record_count(
            count=count, inventory=self.inventory, counted=Decimal("9"),
            actor=self.owner,
        )
        inventory_services.abandon_count(
            count=count, actor=self.owner, reason="Wrong branch"
        )

        self.assertEqual(self.live(Notification.Kind.COUNT_AWAITING).count(), 0)


class OrderApprovalTests(Base):
    def an_order(self):
        from procurement import services as procurement_services
        from procurement.models import Supplier

        supplier = Supplier.objects.create(
            organization=self.org, name="A Supplier", is_active=True
        )
        return procurement_services.raise_order(
            branch=self.branch,
            supplier=supplier,
            lines=[
                {
                    "product": a_product(self.org, name="Soap", sku="SOAP"),
                    "quantity": Decimal("10"),
                    "unit_cost": Decimal("50.00"),
                }
            ],
        )

    def test_submitting_asks_the_approver(self):
        """
        The notification the access model implies: `purchasing.manage` and
        `purchasing.approve` are different people on purpose, and until now the
        approver had no way to learn an order was waiting.
        """
        from procurement import services as procurement_services

        order = self.an_order()
        procurement_services.submit_order(order)

        row = self.live(Notification.Kind.ORDER_AWAITING).get()
        self.assertEqual(row.permission, access.PURCHASING_APPROVE)

    def test_approving_resolves_it(self):
        from procurement import services as procurement_services

        order = self.an_order()
        procurement_services.submit_order(order)
        procurement_services.approve_order(order, actor=self.owner)

        self.assertEqual(self.live(Notification.Kind.ORDER_AWAITING).count(), 0)

    def test_cancelling_resolves_it(self):
        """The other way it stops waiting, and the one easy to forget."""
        from procurement import services as procurement_services

        order = self.an_order()
        procurement_services.submit_order(order)
        procurement_services.cancel_order(order, reason="Supplier went quiet")

        self.assertEqual(self.live(Notification.Kind.ORDER_AWAITING).count(), 0)


class ANotificationCannotFailASaleTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE PROPERTY THE WHOLE DESIGN RESTS ON.

    These raises happen inside the transactions that take money. An exception
    escaping one would roll back a sale — so a shop would be unable to trade
    because of a bug in the code that tells somebody about trading.

    `_safely` swallows and logs. This test breaks the raise on purpose and
    asserts the sale still completes, because that guarantee is worth more than
    any notification in the feed.
    ══════════════════════════════════════════════════════════════════════════
    """

    def test_a_sale_completes_even_when_raising_blows_up(self):
        from sales import services as sales_services

        product = a_product(self.org, name="Blue Band", sku="BB")
        inventory = BranchInventory.objects.create(
            branch=self.branch, product=product,
            quantity=Decimal("20"), reorder_level=Decimal("5"),
        )
        person = a_person(self.org, "Jane Cashier", 10000001)
        staffAssignment.objects.create(
            staff_member=person, branch=self.branch, staff_assignment="CA"
        )
        register = Register.objects.create(
            branch=self.branch, name="Till 1", is_active=True
        )
        shift = RegisterShift.objects.create(
            register=register, operator=person,
            opening_cash=Decimal("0"), status="OPEN",
        )

        with patch(
            "notifications.services.raise_notification",
            side_effect=RuntimeError("the notification code is broken"),
        ):
            sale = sales_services.checkout(
                shift=shift,
                cashier=person,
                lines=[
                    {
                        "product": product,
                        "quantity": Decimal("16"),
                        "discount": Decimal("0"),
                    }
                ],
                payments=[{"method": "CASH", "amount": Decimal("1600.00")}],
            )

        self.assertIsNotNone(sale.pk)
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, Decimal("4"))
        # And nothing was written, which is the cost being accepted here.
        self.assertEqual(Notification.objects.count(), 0)


class EndpointTests(Base):
    def setUp(self):
        super().setUp()
        from notifications import services

        self.first = services.raise_notification(
            kind=Notification.Kind.SHIFT_CLOSED,
            organization_id=self.org.pk,
            permission=access.REPORTS_BRANCH,
            subject="Till 1 closed and the drawer agreed",
        )
        self.second = services.raise_notification(
            kind=Notification.Kind.SHIFT_SHORT,
            organization_id=self.org.pk,
            permission=access.REPORTS_BRANCH,
            subject="Till 2 closed KSh 300 short",
            urgency=Notification.Urgency.ATTEND,
        )

    def test_the_feed_answers_with_the_unread_count_beside_it(self):
        self.as_owner()
        response = self.client.get("/ntf/")
        self.assertEqual(response.status_code, 200, response.content)

        body = response.json()
        self.assertEqual(body["unread"], 2)
        self.assertEqual(len(body["results"]), 2)

    def test_the_feed_never_publishes_the_permission(self):
        """
        ⚠ It is the audience rule. Publishing it tells a cashier which
          authority they hold, which describes other people's access as much as
          their own — and the server has already decided they may see the row.
        """
        self.as_owner()
        body = self.client.get("/ntf/").json()
        self.assertNotIn("permission", body["results"][0])
        self.assertNotIn("actor_account", body["results"][0])

    def test_unread_is_its_own_cheap_endpoint(self):
        self.as_owner()
        response = self.client.get("/ntf/unread")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"unread": 2})

    def test_marking_read_by_id(self):
        self.as_owner()
        response = self.client.post(
            "/ntf/read", {"ids": [self.first.pk]}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"marked": 1, "unread": 1})

    def test_marking_everything_read(self):
        self.as_owner()
        response = self.client.post("/ntf/read", {}, content_type="application/json")
        self.assertEqual(response.json(), {"marked": 2, "unread": 0})

    def test_a_bad_ids_payload_is_refused_rather_than_ignored(self):
        """A caller who sent it believes it worked."""
        self.as_owner()
        response = self.client.post(
            "/ntf/read", {"ids": "all"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400, response.content)

    def test_an_anonymous_caller_is_refused(self):
        self.assertIn(self.client.get("/ntf/").status_code, (401, 403))
        self.assertIn(self.client.get("/ntf/unread").status_code, (401, 403))

    def test_there_is_no_way_to_create_one_through_the_api(self):
        """
        ⚠ A notification is a statement by the system that something happened.
          An endpoint that let a client assert one would let anybody holding a
          token manufacture "M-Pesa payment confirmed" into a manager's feed.
        """
        self.as_owner()
        response = self.client.post(
            "/ntf/",
            {"kind": "payment.confirmed", "subject": "KSh 100000 confirmed"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405, response.content)
        self.assertEqual(Notification.objects.count(), 2)
