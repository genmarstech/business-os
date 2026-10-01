"""
Price lists over HTTP, and the one thing this feature could break.

A till reads a price from the product endpoint and the checkout charges one.
If those two ever come from different code, a customer is charged something
other than the shelf edge said and nobody in the shop can work out who is
right. `SamePriceEverywhereTests` is the test that stops it.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from branches.models import Register, RegisterShift, staffAssignment
from django.test import TestCase
from django.utils import timezone

from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import TenantMembership
from inventory.models import BranchInventory
from sales import services as sales_services
from sales.models import Payment

from catalog.models import PriceList, PriceListEntry

from .factories import (
    a_price_list,
    a_product,
    a_shop,
    a_staff,
    a_subscriber,
    a_till,
)


class Base(TestCase):
    def setUp(self):
        self.org, (self.west, self.karen) = a_shop(
            "Grocers", branches=("Westlands", "Karen")
        )
        self.milk = a_product(self.org, name="Milk", price="100.00")
        self.today = timezone.localdate()

    def sign_in_owner(self, number=90):
        """
        One owner per test, made on demand.

        `genmars_account_id` is unique across the whole table, so minting a
        fresh account on each call meant a test could sign in only once —
        the second collided on the constraint and surfaced as an
        IntegrityError from somewhere that looked nothing like the cause.
        """
        if not hasattr(self, "_owner"):
            self._owner = a_subscriber(
                self.org, TenantMembership.Role.OWNER, number=number
            )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self._owner.pk
        session.save()
        return self._owner

    def post(self, path, payload, **extra):
        return self.client.post(
            path, json.dumps(payload), content_type="application/json", **extra
        )


class SamePriceEverywhereTests(Base):
    """
    ══════════════════════════════════════════════════════════════════════
    THE SHELF EDGE AND THE TILL COME FROM ONE IMPLEMENTATION.

    `catalog.pricing` is called by the product endpoint and by
    `sales.services.checkout`, and by nothing else. These tests read the
    first and then exercise the second, and assert the same number.
    ══════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        super().setUp()
        self.cashier = a_staff(
            self.org, name="Jane Cashier", email="jane@a.co.ke", id_number=1001
        )
        staffAssignment.objects.create(
            staff_member=self.cashier, branch=self.west, staff_assignment="CA"
        )
        register = Register.objects.create(
            branch=self.west, name="Till 1", register_number="T1"
        )
        self.shift = RegisterShift.objects.create(
            register=register, operator=self.cashier, opening_cash=Decimal("0.00")
        )
        BranchInventory.objects.create(
            branch=self.west, product=self.milk, quantity=Decimal("100")
        )

    def shown_price(self, branch):
        self.sign_in_owner()
        body = self.client.get(f"/ctl/products/?branch={branch.pk}").json()
        rows = body["results"] if isinstance(body, dict) else body
        return next(row for row in rows if row["id"] == self.milk.pk)

    def test_a_promotion_reaches_the_till_and_the_receipt_together(self):
        a_price_list(self.org, name="Promotion", precedence=50,
                     prices=[(self.milk, "80.00")])

        shown = self.shown_price(self.west)
        self.assertEqual(shown["price"], "80.00")
        # The base is still there, so a screen can strike it through.
        self.assertEqual(shown["selling_price"], "100.00")
        self.assertEqual(shown["price_list_name"], "Promotion")

        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.milk, "quantity": Decimal("2")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("160.00")}],
        )
        item = sale.items.get()
        self.assertEqual(item.unit_price, Decimal("80.00"))
        self.assertEqual(sale.total, Decimal("160.00"))

    def test_with_no_list_the_two_still_agree(self):
        """The control. Everything above has to fail for the right reason."""
        self.assertEqual(self.shown_price(self.west)["price"], "100.00")
        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.milk, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        self.assertEqual(sale.items.get().unit_price, Decimal("100.00"))

    def test_a_branch_price_does_not_follow_the_product_to_another_branch(self):
        a_price_list(self.org, name="Westlands", precedence=50,
                     prices=[(self.milk, "120.00")], branches=[self.west])
        self.assertEqual(self.shown_price(self.west)["price"], "120.00")
        self.assertEqual(self.shown_price(self.karen)["price"], "100.00")

    def test_the_sale_records_which_list_priced_it(self):
        """
        "Why was this 80 when the shelf says 100" is asked weeks later, by
        which time the promotion has ended and the configuration explains
        nothing.
        """
        promotion = a_price_list(self.org, name="Promotion", precedence=50,
                                 prices=[(self.milk, "80.00")])
        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.milk, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("80.00")}],
        )
        self.assertEqual(sale.items.get().price_list, promotion)

    def test_a_sale_at_the_base_price_names_no_list(self):
        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.milk, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        self.assertIsNone(sale.items.get().price_list)

    def test_ending_a_promotion_does_not_rewrite_what_was_charged(self):
        """
        Every price on a sale line is a copy. The same snapshot rule that
        keeps a supplier's price rise out of last quarter's margin.
        """
        promotion = a_price_list(self.org, name="Promotion", precedence=50,
                                 prices=[(self.milk, "80.00")])
        sale = sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.milk, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("80.00")}],
        )
        promotion.is_active = False
        promotion.save(update_fields=["is_active"])
        PriceListEntry.objects.filter(price_list=promotion).update(
            price=Decimal("5.00")
        )

        sale.refresh_from_db()
        self.assertEqual(sale.items.get().unit_price, Decimal("80.00"))
        self.assertEqual(sale.total, Decimal("80.00"))


class PriceListApiTests(Base):
    def test_an_owner_writes_a_list_with_its_prices_and_branches(self):
        self.sign_in_owner()
        response = self.post(
            "/ctl/price-lists/",
            {
                "organization": self.org.pk,
                "name": "October promotion",
                "precedence": 50,
                "branches": [self.west.pk],
                "entries": [{"product": self.milk.pk, "price": "80.00"}],
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        price_list = PriceList.objects.get(name="October promotion")
        self.assertEqual(price_list.entries.get().price, Decimal("80.00"))
        self.assertEqual(
            [link.branch_id for link in price_list.branch_links.all()],
            [self.west.pk],
        )

    def test_a_cashier_cannot_write_one(self):
        """
        The selling price is the one field a till must not be able to edit,
        and a price list is the selling price wearing a hat.
        """
        jane = a_staff(self.org, name="Jane", email="jane@a.co.ke", id_number=2001)
        staffAssignment.objects.create(
            staff_member=jane, branch=self.west, staff_assignment="CA"
        )
        _, token = identity_services.open_staff_session(a_till(jane, "jane"))

        response = self.post(
            "/ctl/price-lists/",
            {"organization": self.org.pk, "name": "Mine", "precedence": 10},
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(response.status_code, 403, response.content)

        # But they may READ one — a cashier asked why milk is 80 should be
        # able to find out.
        self.assertEqual(
            self.client.get(
                "/ctl/price-lists/", HTTP_AUTHORIZATION=f"Bearer {token}"
            ).status_code,
            200,
        )

    def test_a_line_naming_another_tenants_product_is_refused_by_field(self):
        """
        The nested write guard in identity/scoping.py. It names the offending
        line, so the refusal is usable rather than "something was wrong".
        """
        other, _ = a_shop("Shop B")
        theirs = a_product(other, name="Their Milk")
        self.sign_in_owner()

        response = self.post(
            "/ctl/price-lists/",
            {
                "organization": self.org.pk,
                "name": "Sneaky",
                "precedence": 10,
                "entries": [{"product": theirs.pk, "price": "1.00"}],
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("entries[0].product", response.json())

    def test_a_list_pointed_at_another_tenants_branch_is_refused(self):
        """
        ── THE HOLE THE PLURAL FIELD OPENED ──────────────────────────────
        `branches` is a list of resolved Branch instances, not of dicts.
        The guard recursed into lists of DICTS only, so without the list
        handling added beside this feature it would have found nothing it
        recognised and waved this through.
        """
        other, (their_branch,) = a_shop("Shop B")
        self.sign_in_owner()

        response = self.post(
            "/ctl/price-lists/",
            {
                "organization": self.org.pk,
                "name": "Sneaky",
                "precedence": 10,
                "branches": [their_branch.pk],
            },
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("branches[0]", response.json())

    def test_resending_entries_replaces_them_rather_than_merging(self):
        """
        Merging would make removing a product from a promotion impossible
        through the API — the operation somebody needs on the morning it was
        supposed to end.
        """
        bread = a_product(self.org, name="Bread", price="60.00")
        price_list = a_price_list(
            self.org, name="Promo", precedence=10,
            prices=[(self.milk, "80.00"), (bread, "50.00")],
        )
        self.sign_in_owner()

        response = self.client.patch(
            f"/ctl/price-lists/{price_list.pk}/",
            json.dumps({"entries": [{"product": self.milk.pk, "price": "80.00"}]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            [entry.product_id for entry in price_list.entries.all()], [self.milk.pk]
        )

    def test_renaming_a_list_leaves_its_prices_alone(self):
        """
        An absent key means "leave them alone"; an empty list means "there
        are none now". Collapsing the two would wipe a promotion on a
        rename.
        """
        price_list = a_price_list(self.org, name="Promo", precedence=10,
                                  prices=[(self.milk, "80.00")])
        self.sign_in_owner()
        response = self.client.patch(
            f"/ctl/price-lists/{price_list.pk}/",
            json.dumps({"name": "Renamed"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(price_list.entries.count(), 1)

    def test_in_force_is_not_the_same_question_as_is_active(self):
        """
        A promotion can be active and three weeks away. A screen showing
        only the flag tells a shop their promotion is running when it is
        not.
        """
        a_price_list(
            self.org, name="Next month", precedence=10,
            starts_on=self.today + timedelta(days=30),
        )
        self.sign_in_owner()
        row = self.client.get("/ctl/price-lists/").json()
        rows = row["results"] if isinstance(row, dict) else row
        self.assertTrue(rows[0]["is_active"])
        self.assertFalse(rows[0]["in_force"])

    def test_another_tenants_list_is_not_visible(self):
        other, _ = a_shop("Shop B")
        a_price_list(other, name="Theirs", precedence=10)
        self.sign_in_owner()
        body = self.client.get("/ctl/price-lists/").json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertEqual(rows, [])
