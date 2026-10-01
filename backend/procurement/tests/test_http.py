"""
The buying endpoints, through HTTP, where a real caller stands.

The negative tests are the point, as they are in identity/tests/test_access.py:
a permission system is easy to write so that everybody can do everything and
every positive test still passes. Each refusal here has a positive control
beside it, so "refused" cannot quietly mean "the endpoint is broken for
everybody".
"""

from __future__ import annotations

import json
from decimal import Decimal

from branches.models import staffAssignment
from django.test import TestCase
from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import TenantMembership
from inventory.models import BranchInventory

from procurement import services
from procurement.models import GoodsReceipt, PurchaseOrder, Supplier

from .factories import (
    a_product,
    a_shop,
    a_staff,
    a_subscriber,
    a_supplier,
    a_till,
    assign,
)


class Base(TestCase):
    def setUp(self):
        self.org, (self.westlands, self.karen) = a_shop(
            "Shop A", branches=("Westlands", "Karen")
        )
        self.supplier = a_supplier(self.org)
        self.milk = a_product(self.org, name="Milk", cost="70.00")

    def sign_in_subscriber(self, role, *, number=90):
        account = a_subscriber(self.org, role, number=number)
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()
        return account

    def sign_in_staff(self, *, role, branch, name, email, id_number, username):
        staff = a_staff(self.org, name=name, email=email, id_number=id_number)
        assign(staff, branch, role)
        credential = a_till(staff, username)
        _, token = identity_services.open_staff_session(credential)
        self.token = token
        return staff

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def post(self, path, payload, **extra):
        return self.client.post(
            path, json.dumps(payload), content_type="application/json", **extra
        )

    def an_order(self, *, branch=None, status=None):
        order = services.raise_order(
            branch=branch or self.westlands,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "10"}],
        )
        if status in (
            PurchaseOrder.Status.SUBMITTED,
            PurchaseOrder.Status.APPROVED,
        ):
            services.submit_order(order)
            order.refresh_from_db()
        if status == PurchaseOrder.Status.APPROVED:
            services.approve_order(order, actor=self.approver())
            order.refresh_from_db()
        return order

    def approver(self):
        """
        One owner per test, made on demand.

        `genmars_account_id` is unique across the whole table, so minting a
        fresh account inside this helper meant a test could only raise one
        approved order — the second collided on the constraint and surfaced
        as an IntegrityError from deep inside the service, which points
        nowhere near the helper that caused it.
        """
        if not hasattr(self, "_approver"):
            self._approver = a_subscriber(
                self.org, TenantMembership.Role.OWNER, number=99
            )
        return self._approver


class SubscriberTests(Base):
    def test_an_owner_can_raise_an_order_with_its_lines(self):
        """The control. Every refusal below needs this to pass first."""
        self.sign_in_subscriber(TenantMembership.Role.OWNER)

        response = self.post(
            "/prc/purchase-orders/",
            {
                "branch": self.westlands.pk,
                "supplier": self.supplier.pk,
                "items": [{"product": self.milk.pk, "quantity_ordered": "10"}],
            },
        )

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["total"], "700.00")
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(body["items"][0]["product_name"], "Milk")

    def test_an_accountant_reads_orders_and_raises_none(self):
        self.sign_in_subscriber(TenantMembership.Role.ACCOUNTANT)
        self.an_order()

        self.assertEqual(self.client.get("/prc/purchase-orders/").status_code, 200)
        refused = self.post(
            "/prc/purchase-orders/",
            {
                "branch": self.westlands.pk,
                "supplier": self.supplier.pk,
                "items": [{"product": self.milk.pk, "quantity_ordered": "1"}],
            },
        )
        self.assertEqual(refused.status_code, 403)

    def test_a_line_naming_another_shops_product_is_refused(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE HOLE THE NESTED WALK IN identity/scoping.py WAS ADDED FOR.

        The header names this shop's branch and this shop's supplier, so a
        flat read of the write finds nothing wrong. The product is somebody
        else's, and before `organisations_referenced` recursed into nested
        lists, the guard waved it through — the service's own check caught
        it, which is exactly the "enforced in two places" state that file
        exists to avoid.
        ══════════════════════════════════════════════════════════════════
        """
        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        other_org, _ = a_shop("Shop B")
        theirs = a_product(other_org, name="Sugar")

        response = self.post(
            "/prc/purchase-orders/",
            {
                "branch": self.westlands.pk,
                "supplier": self.supplier.pk,
                "items": [{"product": theirs.pk, "quantity_ordered": "1"}],
            },
        )

        self.assertEqual(response.status_code, 400, response.content)
        # The refusal names the line, and says nothing that confirms the
        # product exists.
        self.assertIn("items[0].product", response.json())
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_one_shop_never_sees_anothers_orders(self):
        other_org, (other_branch,) = a_shop("Shop B")
        services.raise_order(
            branch=other_branch,
            supplier=a_supplier(other_org, name="Theirs"),
            lines=[{"product": a_product(other_org), "quantity": "1"}],
        )
        mine = self.an_order()

        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        body = self.client.get("/prc/purchase-orders/").json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertEqual([row["id"] for row in rows], [mine.pk])

    def test_status_cannot_be_moved_by_a_patch(self):
        """
        An approval with no approver, no timestamp and no permission check
        distinct from editing a note is three quarters of what an approval
        is not.
        """
        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        order = self.an_order()

        response = self.client.patch(
            f"/prc/purchase-orders/{order.pk}/",
            json.dumps({"status": "approved"}),
            content_type="application/json",
        )
        order.refresh_from_db()
        self.assertIn(response.status_code, (200, 400))
        self.assertEqual(order.status, PurchaseOrder.Status.DRAFT)


class SeparationOfDutiesTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE CONTROL THIS WHOLE APP IS SHAPED AROUND.

    A purchasing officer raises and sends. Somebody else approves. It is built
    the way a cashier is stopped from voiding their own sale — not by an
    identity check in a view, but by a permission the first person does not
    hold — because an identity check is one `if` somebody deletes, and a
    missing permission is a role definition somebody has to deliberately
    change.
    ══════════════════════════════════════════════════════════════════════════
    """

    def test_a_purchasing_officer_raises_and_sends(self):
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.PurchasingOfficer,
            branch=self.westlands,
            name="Peter Buyer",
            email="peter@a.co.ke",
            id_number=2001,
            username="peter",
        )

        created = self.post(
            "/prc/purchase-orders/",
            {
                "branch": self.westlands.pk,
                "supplier": self.supplier.pk,
                "items": [{"product": self.milk.pk, "quantity_ordered": "10"}],
            },
            **self.auth(),
        )
        self.assertEqual(created.status_code, 201, created.content)

        order_id = created.json()["id"]
        sent = self.post(
            f"/prc/purchase-orders/{order_id}/submit/", {}, **self.auth()
        )
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertEqual(sent.json()["status"], "submitted")

    def test_the_officer_who_raised_it_cannot_approve_it(self):
        staff = self.sign_in_staff(
            role=staffAssignment.StaffRoles.PurchasingOfficer,
            branch=self.westlands,
            name="Peter Buyer",
            email="peter@a.co.ke",
            id_number=2001,
            username="peter",
        )
        order = services.raise_order(
            branch=self.westlands,
            supplier=self.supplier,
            lines=[{"product": self.milk, "quantity": "10"}],
            actor=staff,
        )
        services.submit_order(order)

        refused = self.post(
            f"/prc/purchase-orders/{order.pk}/approve/", {}, **self.auth()
        )
        self.assertEqual(refused.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrder.Status.SUBMITTED)

    def test_a_branch_manager_approves_and_cannot_raise(self):
        """
        The other half. A manager who could also raise an order would be both
        signatures on the same piece of paper.
        """
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.AssistantManager,
            branch=self.westlands,
            name="Mary Manager",
            email="mary@a.co.ke",
            id_number=2002,
            username="mary",
        )
        order = self.an_order(status=PurchaseOrder.Status.SUBMITTED)

        approved = self.post(
            f"/prc/purchase-orders/{order.pk}/approve/", {}, **self.auth()
        )
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], "approved")
        self.assertEqual(approved.json()["approved_by_name"], "Mary Manager")

        refused = self.post(
            "/prc/purchase-orders/",
            {
                "branch": self.westlands.pk,
                "supplier": self.supplier.pk,
                "items": [{"product": self.milk.pk, "quantity_ordered": "1"}],
            },
            **self.auth(),
        )
        self.assertEqual(refused.status_code, 403)

    def test_a_cashier_reaches_none_of_it(self):
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.Cashier,
            branch=self.westlands,
            name="Jane Cashier",
            email="jane@a.co.ke",
            id_number=2003,
            username="jane",
        )
        self.an_order()

        for path in ("/prc/purchase-orders/", "/prc/suppliers/", "/prc/goods-receipts/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, **self.auth()).status_code, 403)

    def test_a_purchasing_officer_cannot_adjust_stock_by_hand(self):
        """
        "PO" used to be mapped onto the inventory clerk's permissions, which
        included INVENTORY_ADJUST. A buyer who can also move a quantity by
        hand can make the difference between what was ordered and what
        arrived disappear without a document.
        """
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.PurchasingOfficer,
            branch=self.westlands,
            name="Peter Buyer",
            email="peter@a.co.ke",
            id_number=2001,
            username="peter",
        )
        inventory = BranchInventory.objects.create(
            branch=self.westlands, product=self.milk, quantity=Decimal("5")
        )
        refused = self.post(
            f"/invt/inventory/{inventory.pk}/adjust/",
            {"quantity": "100", "reason": "DELIVERY"},
            **self.auth(),
        )
        self.assertEqual(refused.status_code, 403)


class BranchTests(Base):
    def test_an_officer_cannot_act_on_another_branchs_order(self):
        """
        One person can be a purchasing officer at Westlands and nothing at
        Karen. `permissions` on the viewset only asks whether they may submit
        AT ALL; the action asks again with the order's branch.
        """
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.PurchasingOfficer,
            branch=self.westlands,
            name="Peter Buyer",
            email="peter@a.co.ke",
            id_number=2001,
            username="peter",
        )
        karen_order = self.an_order(branch=self.karen)

        # Not even visible: branch scoping narrows the queryset first, so the
        # order reads as a record that does not exist.
        self.assertEqual(
            self.client.get(
                f"/prc/purchase-orders/{karen_order.pk}/", **self.auth()
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                f"/prc/purchase-orders/{karen_order.pk}/submit/", {}, **self.auth()
            ).status_code,
            404,
        )


class ReceivingOverHttpTests(Base):
    def test_a_storeman_books_in_a_delivery_and_the_stock_moves(self):
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.InventoryClerk,
            branch=self.westlands,
            name="Sam Store",
            email="sam@a.co.ke",
            id_number=2004,
            username="sam",
        )
        order = self.an_order(status=PurchaseOrder.Status.APPROVED)
        line = order.items.get()

        response = self.post(
            f"/prc/purchase-orders/{order.pk}/receive/",
            {
                "lines": [{"item": line.pk, "quantity": "10"}],
                "delivery_note": "DN-9001",
                "idempotency_key": "dn-9001",
            },
            **self.auth(),
        )

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["order"]["status"], "received")
        self.assertEqual(body["receipt"]["delivery_note"], "DN-9001")
        self.assertEqual(body["receipt"]["received_by_name"], "Sam Store")

        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.westlands, product=self.milk
            ).quantity,
            Decimal("10.00"),
        )

    def test_the_same_delivery_sent_twice_books_once(self):
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.InventoryClerk,
            branch=self.westlands,
            name="Sam Store",
            email="sam@a.co.ke",
            id_number=2004,
            username="sam",
        )
        order = self.an_order(status=PurchaseOrder.Status.APPROVED)
        line = order.items.get()
        payload = {
            "lines": [{"item": line.pk, "quantity": "10"}],
            "idempotency_key": "dn-9001",
        }

        first = self.post(
            f"/prc/purchase-orders/{order.pk}/receive/", payload, **self.auth()
        )
        second = self.post(
            f"/prc/purchase-orders/{order.pk}/receive/", payload, **self.auth()
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertEqual(
            BranchInventory.objects.get(
                branch=self.westlands, product=self.milk
            ).quantity,
            Decimal("10.00"),
        )

    def test_over_receipt_is_a_400_a_person_can_act_on(self):
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.InventoryClerk,
            branch=self.westlands,
            name="Sam Store",
            email="sam@a.co.ke",
            id_number=2004,
            username="sam",
        )
        order = self.an_order(status=PurchaseOrder.Status.APPROVED)
        line = order.items.get()

        response = self.post(
            f"/prc/purchase-orders/{order.pk}/receive/",
            {"lines": [{"item": line.pk, "quantity": "12"}]},
            **self.auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("quantity", response.json())
        self.assertEqual(GoodsReceipt.objects.count(), 0)


class SupplierTests(Base):
    def test_a_supplier_belongs_to_the_shop_that_created_it(self):
        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        response = self.post(
            "/prc/suppliers/",
            {"organization": self.org.pk, "name": "Kevian", "phone_number": "0722000111"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            Supplier.objects.get(name="Kevian").organization_id, self.org.pk
        )

    def test_a_supplier_cannot_be_filed_under_another_shop(self):
        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        other_org, _ = a_shop("Shop B")

        response = self.post(
            "/prc/suppliers/", {"organization": other_org.pk, "name": "Kevian"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("organization", response.json())

    def test_two_shops_may_both_buy_from_brookside(self):
        """
        A uniqueness constraint that spans tenants is an oracle: the error
        tells whoever trips it that a supplier exists in a shop they cannot
        see.
        """
        other_org, _ = a_shop("Shop B")
        a_supplier(other_org, name="Brookside")
        self.assertEqual(Supplier.objects.filter(name="Brookside").count(), 2)


class BuyingReportApiTests(Base):
    """
    The reports over HTTP. Who may read them is a decision in its own right —
    see the banner on `BuyingReportViewSet` — so it is tested rather than
    assumed.
    """

    ROUTES = (
        "/prc/reports/overview/",
        "/prc/reports/by-supplier/",
        "/prc/reports/by-product/",
        "/prc/reports/outstanding/",
        "/prc/reports/reliability/",
    )

    def test_every_report_refuses_an_anonymous_caller(self):
        for route in self.ROUTES:
            response = self.client.get(route)
            self.assertIn(response.status_code, (401, 403), route)

    def test_a_purchasing_officer_may_read_them(self):
        """
        The decision this viewset makes, as a test. A purchasing officer
        holds no reporting permission at all; gating buying reports behind
        one would hide what a supplier costs from the only person whose job
        is to negotiate it.
        """
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.PurchasingOfficer,
            branch=self.westlands,
            name="Peter Buyer",
            email="peter@a.co.ke",
            id_number=3001,
            username="peter",
        )
        for route in self.ROUTES:
            response = self.client.get(route, **self.auth())
            self.assertEqual(response.status_code, 200, f"{route}: {response.content}")

    def test_a_cashier_is_refused(self):
        """The positive control above makes this mean something."""
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.Cashier,
            branch=self.westlands,
            name="Jane Cashier",
            email="jane@a.co.ke",
            id_number=3002,
            username="jane",
        )
        for route in self.ROUTES:
            response = self.client.get(route, **self.auth())
            self.assertEqual(response.status_code, 403, route)

    def test_every_figure_crosses_the_wire_as_a_string(self):
        """
        ── DRF RENDERS A BARE Decimal AS A float ───────────────────────────
        These reports are plain dicts, not serialisers, so they miss
        DRF's own DecimalField treatment and 19.99 would leave as
        19.989999999999998. `exact()` is what stops that, and this is what
        notices if a new action forgets to call it.
        """
        self.sign_in_subscriber(TenantMembership.Role.OWNER)
        order = self.an_order(status=PurchaseOrder.Status.APPROVED)
        BranchInventory.objects.create(
            branch=self.westlands, product=self.milk, quantity=Decimal("0")
        )
        services.receive_goods(
            order, lines=[{"item": order.items.first(), "quantity": Decimal("10")}]
        )

        body = self.client.get("/prc/reports/overview/").json()
        self.assertIsInstance(body["received_value"], str)
        self.assertEqual(body["received_value"], "700.00")

        suppliers = self.client.get("/prc/reports/by-supplier/").json()["suppliers"]
        self.assertIsInstance(suppliers[0]["received_value"], str)

        position = self.client.get("/prc/reports/outstanding/").json()
        self.assertIsInstance(position["committed"], str)

    def test_a_branch_manager_sees_their_own_branch_and_not_the_other(self):
        """
        The order LIST is confined by `branch_path`; the report has to stop
        in the same place or it is a way round the scoping of the screen it
        summarises.
        """
        self.sign_in_staff(
            role=staffAssignment.StaffRoles.AssistantManager,
            branch=self.westlands,
            name="Grace Manager",
            email="grace@a.co.ke",
            id_number=3003,
            username="grace",
        )
        for branch in (self.westlands, self.karen):
            BranchInventory.objects.create(
                branch=branch, product=self.milk, quantity=Decimal("0")
            )
            order = self.an_order(branch=branch, status=PurchaseOrder.Status.APPROVED)
            services.receive_goods(
                order,
                lines=[{"item": order.items.first(), "quantity": Decimal("10")}],
            )

        body = self.client.get("/prc/reports/overview/", **self.auth()).json()
        self.assertEqual(body["received_value"], "700.00")
        self.assertEqual(body["deliveries"], 1)
