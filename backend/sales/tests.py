"""
The till, tested where a bug costs somebody money.

Every test here is one where failure is a shop giving away stock, charging a
customer twice, or one tenant reading another's takings — not a cosmetic
defect. The arithmetic tests matter as much as the isolation ones: a VAT split
that is wrong by a cent is wrong on every receipt the business ever issues.
"""

from __future__ import annotations

from datetime import datetime, time
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct, TaxRule
from identity.authentication import StaffPrincipal
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory.models import BranchInventory, StockMovement
from organisations.models import BusinessOrganization, OrganizationStaff

from . import reports, services
from .models import Customer, Payment, Refund, Sale, SaleItem


def a_shop(name="Shop A"):
    org = BusinessOrganization.objects.create(name=name)
    branch = Branches.objects.create(
        organization=org,
        branch_name=f"{name} Main",
        branch_location="Nairobi",
        branch_allocation="Ground floor",
        branch_manager="A Manager",
        is_active=True,
    )
    staff = OrganizationStaff.objects.create(
        organization=org,
        full_name="Jane Cashier",
        email=f"jane@{name.replace(' ', '').lower()}.co.ke",
        phone_number="+254700000001",
        address="Nairobi",
        id_number=abs(hash(name)) % 100000,
    )
    register = Register.objects.create(
        branch=branch, name="Till 1", register_number="T1"
    )
    shift = RegisterShift.objects.create(
        register=register, operator=staff, opening_cash=Decimal("1000.00")
    )
    return org, branch, staff, register, shift


def a_product(org, *, name="Milk", price="100.00", cost="70.00", rule=None):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
    return CatalogCategoryProduct.objects.create(
        organization=org,
        category=category,
        name=name,
        sku=f"SKU-{name}",
        cost_price=Decimal(cost),
        selling_price=Decimal(price),
        tax_rule=rule,
    )


def stock(branch, product, quantity="10"):
    return BranchInventory.objects.create(
        branch=branch, product=product, quantity=Decimal(quantity)
    )


class CheckoutTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org)
        self.stock = stock(self.branch, self.product, "10")

    def sell(self, quantity="2", **kwargs):
        return services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            payments=kwargs.pop(
                "payments",
                [{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
            ),
            **kwargs,
        )

    def test_a_sale_is_written_with_its_lines_and_payment(self):
        sale = self.sell()
        self.assertEqual(sale.status, Sale.Status.COMPLETED)
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.payments.count(), 1)
        self.assertEqual(sale.total, Decimal("200.00"))

    def test_stock_goes_down_and_says_why(self):
        """
        Blueprint §10: stock changes through auditable movements. A quantity
        that moved with no movement behind it is what makes a stock take
        impossible to reconcile.
        """
        sale = self.sell("3", payments=[
            {"method": Payment.Method.CASH, "amount": Decimal("300.00")}
        ])
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("7.00"))

        movement = StockMovement.objects.get(inventory=self.stock)
        self.assertEqual(movement.movement_type, "SALE")
        self.assertEqual(movement.quantity_before, Decimal("10.00"))
        self.assertEqual(movement.quantity_after, Decimal("7.00"))
        self.assertIn(str(sale.number), movement.reference)

    def test_selling_more_than_there_is_takes_nothing(self):
        with self.assertRaises(services.SaleError):
            self.sell("50", payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("5000.00")}
            ])

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.00"))
        self.assertEqual(Sale.objects.count(), 0)
        # The whole point of the transaction: no half-written sale behind it.
        self.assertEqual(SaleItem.objects.count(), 0)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_underpaying_is_refused(self):
        with self.assertRaises(services.SaleError):
            self.sell(payments=[
                {"method": Payment.Method.CASH, "amount": Decimal("50.00")}
            ])
        self.assertEqual(Sale.objects.count(), 0)

    def test_cash_overpayment_becomes_change(self):
        """
        A 500 note against a 200 total: 200 taken, 300 handed back. Recording
        the full 500 as taken would make the drawer 300 over at close, every
        time, and nobody would know which sale did it.
        """
        sale = self.sell(payments=[
            {"method": Payment.Method.CASH, "amount": Decimal("500.00")}
        ])
        payment = sale.payments.get()
        self.assertEqual(payment.amount, Decimal("200.00"))
        self.assertEqual(payment.tendered, Decimal("500.00"))
        self.assertEqual(payment.change_given, Decimal("300.00"))

    def test_overpaying_by_card_is_refused(self):
        """
        Change is only meaningful in cash. On a card line an excess is a typo,
        and taking it silently makes the reconciliation wrong.
        """
        with self.assertRaises(services.SaleError):
            self.sell(payments=[
                {"method": Payment.Method.CARD, "amount": Decimal("500.00")}
            ])

    def test_a_split_payment_is_just_two_rows(self):
        sale = self.sell(payments=[
            {"method": Payment.Method.CASH, "amount": Decimal("150.00")},
            {"method": Payment.Method.MPESA, "amount": Decimal("50.00"),
             "reference": "RKT8Z2M1QP"},
        ])
        self.assertEqual(sale.payments.count(), 2)
        self.assertEqual(sale.amount_paid, Decimal("200.00"))
        self.assertEqual(
            sale.payments.get(method="mpesa").reference, "RKT8Z2M1QP"
        )

    def test_an_account_sale_needs_somebody_to_put_it_on(self):
        with self.assertRaises(services.SaleError):
            self.sell(payments=[
                {"method": Payment.Method.CREDIT, "amount": Decimal("200.00")}
            ])

    def test_an_account_sale_moves_the_customer_balance(self):
        customer = Customer.objects.create(
            organization=self.org, full_name="A Debtor", phone_number="+254711000000"
        )
        self.sell(
            customer=customer,
            payments=[
                {"method": Payment.Method.CREDIT, "amount": Decimal("200.00")}
            ],
        )
        customer.refresh_from_db()
        self.assertEqual(customer.credit_balance, Decimal("200.00"))

    def test_a_closed_shift_cannot_sell(self):
        self.shift.status = "CLOSED"
        self.shift.save()
        with self.assertRaises(services.SaleError):
            self.sell()

    def test_numbering_starts_high_and_is_per_organisation(self):
        """
        A platform-wide counter would tell every tenant how much business
        every other tenant is doing. Two shops both start at 1000.
        """
        first = self.sell()
        self.assertEqual(first.number, services.FIRST_SALE_NUMBER)

        other_org, other_branch, other_staff, _, other_shift = a_shop("Shop B")
        other_product = a_product(other_org)
        stock(other_branch, other_product)
        theirs = services.checkout(
            shift=other_shift,
            cashier=other_staff,
            lines=[{"product": other_product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        self.assertEqual(theirs.number, services.FIRST_SALE_NUMBER)

    def test_a_receipt_is_issued_once(self):
        sale = self.sell()
        self.assertTrue(sale.receipt.number)
        again = services.issue_receipt(sale)
        self.assertEqual(again.pk, sale.receipt.pk)

    def test_a_reprint_is_counted(self):
        sale = self.sell()
        services.reprint_receipt(sale.receipt)
        services.reprint_receipt(sale.receipt, delivered_to="+254700111222")
        sale.receipt.refresh_from_db()
        self.assertEqual(sale.receipt.reprint_count, 2)
        self.assertEqual(sale.receipt.delivered_to, "+254700111222")


class IdempotencyTests(TestCase):
    """
    Blueprint §11. A till on a bad link sends a checkout, never sees the
    answer, and sends it again. Without a key that is a double charge and a
    double stock decrement, and the evidence looks exactly like two customers
    buying the same thing a second apart.
    """

    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org)
        self.stock = stock(self.branch, self.product, "10")

    def send(self):
        return services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("2")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
            idempotency_key="till-1-txn-000123",
        )

    def test_the_same_key_returns_the_same_sale(self):
        first = self.send()
        second = self.send()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Sale.objects.count(), 1)

    def test_a_retry_does_not_take_the_stock_twice(self):
        self.send()
        self.send()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8.00"))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_a_retry_does_not_charge_twice(self):
        self.send()
        self.send()
        self.assertEqual(Payment.objects.count(), 1)


class TaxTests(TestCase):
    """
    A VAT split that is wrong by a cent is wrong on every receipt the business
    ever issues, and it is wrong in the direction of the tax authority.
    """

    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()

    def sell(self, product, quantity="1", paid="116.00"):
        stock(self.branch, product, "10")
        return services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": product, "quantity": Decimal(quantity)}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal(paid)}],
        )

    def test_an_inclusive_rate_comes_out_of_the_shelf_price(self):
        """
        KSh 116 on the label at 16% inclusive: the customer pays 116, and
        16.00 of it is tax. The total must NOT become 134.56.
        """
        rule = TaxRule.objects.create(
            organization=self.org, name="VAT 16%", rate=Decimal("16.00"),
            is_inclusive=True, is_default=True,
        )
        product = a_product(self.org, name="Bread", price="116.00", rule=rule)
        sale = self.sell(product)

        self.assertEqual(sale.total, Decimal("116.00"))
        self.assertEqual(sale.tax_total, Decimal("16.00"))

    def test_an_exclusive_rate_is_added_on_top(self):
        """KSh 100 at 16% exclusive: the customer pays 116."""
        rule = TaxRule.objects.create(
            organization=self.org, name="VAT 16% excl", rate=Decimal("16.00"),
            is_inclusive=False, is_default=True,
        )
        product = a_product(self.org, name="Sugar", price="100.00", rule=rule)
        sale = self.sell(product)

        self.assertEqual(sale.total, Decimal("116.00"))
        self.assertEqual(sale.tax_total, Decimal("16.00"))

    def test_no_rule_means_no_tax_rather_than_a_guess(self):
        product = a_product(self.org, name="Salt", price="100.00")
        sale = self.sell(product, paid="100.00")
        self.assertEqual(sale.tax_total, Decimal("0.00"))
        self.assertEqual(sale.total, Decimal("100.00"))

    def test_the_organisation_default_applies_to_a_product_with_no_rule(self):
        TaxRule.objects.create(
            organization=self.org, name="VAT 16%", rate=Decimal("16.00"),
            is_inclusive=True, is_default=True,
        )
        product = a_product(self.org, name="Rice", price="116.00")
        sale = self.sell(product)
        self.assertEqual(sale.tax_total, Decimal("16.00"))

    def test_the_rate_is_copied_onto_the_line(self):
        """
        A receipt reprinted after the rate changes has to show the rate that
        was actually charged, not today's.
        """
        rule = TaxRule.objects.create(
            organization=self.org, name="VAT 16%", rate=Decimal("16.00"),
            is_inclusive=True, is_default=True,
        )
        product = a_product(self.org, name="Tea", price="116.00", rule=rule)
        sale = self.sell(product)

        rule.rate = Decimal("18.00")
        rule.save()

        line = sale.items.get()
        self.assertEqual(line.tax_rate, Decimal("16.00"))
        self.assertEqual(line.tax_amount, Decimal("16.00"))


class SnapshotTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org, name="Milk", price="100.00")
        stock(self.branch, self.product, "10")

    def test_a_price_rise_does_not_rewrite_what_was_paid(self):
        sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )

        self.product.selling_price = Decimal("250.00")
        self.product.name = "Milk 500ml (new packaging)"
        self.product.save()

        line = sale.items.get()
        self.assertEqual(line.unit_price, Decimal("100.00"))
        self.assertEqual(line.product_name, "Milk")
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("100.00"))


class VoidTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org)
        self.stock = stock(self.branch, self.product, "10")
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("2")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
        )

    def test_a_void_puts_the_stock_back(self):
        services.void_sale(self.sale, reason="Rung up twice")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.00"))
        self.assertEqual(
            StockMovement.objects.filter(movement_type="RETURN").count(), 1
        )

    def test_a_void_keeps_the_sale_and_says_why(self):
        """§10 again: the row stays, readable, with the reason attached."""
        services.void_sale(self.sale, reason="Rung up twice")
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.status, Sale.Status.VOIDED)
        self.assertEqual(self.sale.void_reason, "Rung up twice")
        self.assertEqual(self.sale.items.count(), 1)
        self.assertEqual(self.sale.payments.count(), 1)

    def test_a_void_needs_a_reason(self):
        with self.assertRaises(services.SaleError):
            services.void_sale(self.sale, reason="   ")

    def test_a_refunded_sale_cannot_be_voided(self):
        services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            lines=[{"sale_item": self.sale.items.get(), "quantity": Decimal("1")}],
            reason="Customer changed their mind",
            method=Payment.Method.CASH,
        )
        with self.assertRaises(services.SaleError):
            services.void_sale(self.sale, reason="Too late")


class RefundTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop()
        self.product = a_product(self.org, price="100.00")
        self.stock = stock(self.branch, self.product, "10")
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("4")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("400.00")}],
        )
        self.line = self.sale.items.get()

    def refund(self, quantity="1", method=Payment.Method.CASH, **kwargs):
        """
        `method` is a named parameter rather than part of **kwargs, because
        kwargs is spread into the LINE and a refund method is not a property
        of a line. Cash by default: it is what a counter does, and the tests
        that care say otherwise.
        """
        return services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal(quantity),
                    **kwargs}],
            reason="Customer returned it",
            method=method,
        )

    def test_the_original_sale_is_untouched(self):
        """
        Blueprint §10's worked example. Sale #1000 KSh 400, refund #2000
        -KSh 100, and the sale still reads 400.
        """
        refund = self.refund()
        self.sale.refresh_from_db()

        self.assertEqual(self.sale.total, Decimal("400.00"))
        self.assertEqual(self.sale.status, Sale.Status.COMPLETED)
        self.assertEqual(refund.total, Decimal("100.00"))
        self.assertEqual(refund.sale_id, self.sale.pk)
        self.assertEqual(refund.number, services.FIRST_REFUND_NUMBER)

    def test_a_restocked_return_goes_back_on_the_shelf(self):
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("6.00"))

        self.refund("2")
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("8.00"))

    def test_a_damaged_return_does_not(self):
        """A returned shirt is stock again; a returned half-eaten meal is not."""
        self.refund("2", restock=False)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("6.00"))

    def test_the_same_line_cannot_be_refunded_past_what_was_sold(self):
        """
        Three partial refunds of two each must not return six of four. The
        outstanding quantity is computed from the refunds already written, not
        from a counter that can drift.
        """
        self.refund("2")
        self.refund("2")
        with self.assertRaises(services.SaleError):
            self.refund("1")

    def test_a_discount_is_honoured_on_the_way_back(self):
        """
        Refunding the shelf price of a discounted item hands back money that
        was never taken.
        """
        discounted = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{
                "product": self.product,
                "quantity": Decimal("2"),
                "discount": Decimal("50.00"),
            }],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("150.00")}],
        )
        line = discounted.items.get()
        self.assertEqual(line.line_total, Decimal("150.00"))

        refund = services.refund_sale(
            sale=discounted,
            branch=self.branch,
            processed_by=self.staff,
            lines=[{"sale_item": line, "quantity": Decimal("1")}],
            reason="One back",
            method=Payment.Method.CASH,
        )
        self.assertEqual(refund.total, Decimal("75.00"))

    def test_a_refund_needs_a_reason(self):
        with self.assertRaises(services.SaleError):
            services.refund_sale(
                sale=self.sale,
                branch=self.branch,
                processed_by=self.staff,
                lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
                reason="",
                method=Payment.Method.CASH,
            )

    def test_a_refund_is_retry_safe(self):
        first = services.refund_sale(
            sale=self.sale, branch=self.branch, processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
            reason="Returned", method=Payment.Method.CASH,
            idempotency_key="till-1-rf-0001",
        )
        second = services.refund_sale(
            sale=self.sale, branch=self.branch, processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
            reason="Returned", method=Payment.Method.CASH,
            idempotency_key="till-1-rf-0001",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Refund.objects.count(), 1)

    def test_a_refund_cannot_be_processed_into_another_shop(self):
        _, their_branch, _, _, _ = a_shop("Shop B")
        with self.assertRaises(services.SaleError):
            services.refund_sale(
                sale=self.sale,
                branch=their_branch,
                processed_by=self.staff,
                lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
                reason="Wrong shop",
                method=Payment.Method.CASH,
            )


class SaleIsolationTests(TestCase):
    """
    One shop must not be able to sell another shop's stock, read another
    shop's takings, or refund another shop's sale.

    The service-level tests here are the backstop for the API-level ones: the
    viewset resolves foreign keys out of unfiltered querysets, which is what a
    PrimaryKeyRelatedField does, so the check has to exist below it as well.
    """

    def setUp(self):
        self.a = a_shop("Shop A")
        self.b = a_shop("Shop B")
        self.a_product = a_product(self.a[0], name="A Milk")
        self.b_product = a_product(self.b[0], name="B Milk")
        stock(self.a[1], self.a_product, "10")
        stock(self.b[1], self.b_product, "10")

    def test_a_till_cannot_sell_another_shops_product(self):
        with self.assertRaises(services.SaleError):
            services.checkout(
                shift=self.a[4],
                cashier=self.a[2],
                lines=[{"product": self.b_product, "quantity": Decimal("1")}],
                payments=[
                    {"method": Payment.Method.CASH, "amount": Decimal("100.00")}
                ],
            )
        self.assertEqual(Sale.objects.count(), 0)

    def test_a_till_cannot_put_a_sale_on_another_shops_customer(self):
        theirs = Customer.objects.create(
            organization=self.b[0], full_name="Their Debtor"
        )
        with self.assertRaises(services.SaleError):
            services.checkout(
                shift=self.a[4],
                cashier=self.a[2],
                customer=theirs,
                lines=[{"product": self.a_product, "quantity": Decimal("1")}],
                payments=[
                    {"method": Payment.Method.CREDIT, "amount": Decimal("100.00")}
                ],
            )


class SaleApiIsolationTests(TestCase):
    """
    The same properties through the HTTP surface, which is where a real
    attacker stands.
    """

    def setUp(self):
        self.a_org, self.a_branch, self.a_staff, _, self.a_shift = a_shop("Shop A")
        self.b_org, self.b_branch, self.b_staff, _, self.b_shift = a_shop("Shop B")

        self.a_product = a_product(self.a_org, name="A Milk")
        self.b_product = a_product(self.b_org, name="B Milk")
        stock(self.a_branch, self.a_product, "10")
        stock(self.b_branch, self.b_product, "10")

        # A subscriber who belongs to Shop A and nothing else.
        self.account = PlatformAccount.objects.create(
            genmars_account_id=501, email="owner@a.co.ke", full_name="A Owner"
        )
        TenantMembership.objects.create(
            account=self.account, organization=self.a_org
        )

        self.b_sale = services.checkout(
            shift=self.b_shift,
            cashier=self.b_staff,
            lines=[{"product": self.b_product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )

    def sign_in(self):
        from identity.authentication import SUBSCRIBER_SESSION_KEY

        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()

    def test_another_shops_sales_are_not_in_the_list(self):
        self.sign_in()
        response = self.client.get("/sls/sales/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        rows = body["results"] if isinstance(body, dict) else body
        self.assertEqual(rows, [])

    def test_another_shops_sale_is_a_404_not_a_403(self):
        """
        A 403 confirms the row exists, which is an enumeration oracle in a
        different costume. Same rule as gen-portal's selectors.
        """
        self.sign_in()
        response = self.client.get(f"/sls/sales/{self.b_sale.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_checkout_refuses_another_shops_shift(self):
        self.sign_in()
        response = self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": self.b_shift.pk,
                "cashier": self.b_staff.pk,
                "lines": [{"product": self.b_product.pk, "quantity": "1"}],
                "payments": [{"method": "cash", "amount": "100.00"}],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Sale.objects.filter(organization=self.b_org).count(), 1)

    def test_checkout_works_for_a_shop_the_caller_belongs_to(self):
        """
        The positive control. Without it every isolation test above would
        still pass if checkout were simply broken for everybody.
        """
        self.sign_in()
        response = self.client.post(
            "/sls/sales/checkout/",
            {
                "shift": self.a_shift.pk,
                "cashier": self.a_staff.pk,
                "lines": [{"product": self.a_product.pk, "quantity": "2"}],
                "payments": [{"method": "cash", "amount": "200.00"}],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["total"], "200.00")
        self.assertEqual(Sale.objects.filter(organization=self.a_org).count(), 1)

    def test_an_anonymous_caller_gets_nothing(self):
        self.assertIn(self.client.get("/sls/sales/").status_code, (401, 403))
        self.assertIn(
            self.client.post("/sls/sales/checkout/", {}, content_type="application/json")
            .status_code,
            (401, 403),
        )

    def test_a_sale_cannot_be_amended_over_http(self):
        """
        There is no PATCH on a financial record, and the absence is the
        feature — blueprint §10.
        """
        self.sign_in()
        sale = services.checkout(
            shift=self.a_shift,
            cashier=self.a_staff,
            lines=[{"product": self.a_product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        response = self.client.patch(
            f"/sls/sales/{sale.pk}/",
            {"total": "1.00"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 405)
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("100.00"))


class ReportTests(TestCase):
    """
    A report is the easiest place in a multi-tenant system to leak everything
    at once — one aggregate over an unscoped table and a shop is reading the
    platform's turnover. These tests are as much about what the numbers do NOT
    include as about what they do.
    """

    def setUp(self):
        self.a_org, self.a_branch, self.a_staff, _, self.a_shift = a_shop("Shop A")
        self.b_org, self.b_branch, self.b_staff, _, self.b_shift = a_shop("Shop B")

        rule = TaxRule.objects.create(
            organization=self.a_org, name="VAT 16%", rate=Decimal("16.00"),
            is_inclusive=True, is_default=True,
        )
        self.product = a_product(
            self.a_org, name="Milk", price="116.00", cost="50.00", rule=rule
        )
        stock(self.a_branch, self.product, "100")

        self.b_product = a_product(self.b_org, name="B Milk", price="100.00")
        stock(self.b_branch, self.b_product, "100")

        self.account = PlatformAccount.objects.create(
            genmars_account_id=601, email="owner@a.co.ke", full_name="A Owner"
        )
        TenantMembership.objects.create(account=self.account, organization=self.a_org)

    def sell(self, quantity="2"):
        return services.checkout(
            shift=self.a_shift,
            cashier=self.a_staff,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("232.00")}],
        )

    def window(self):
        """Today, as whole local days — the same window the API defaults to."""
        today = timezone.localtime().date()
        tz = timezone.get_current_timezone()
        return (
            timezone.make_aware(datetime.combine(today, time.min), tz),
            timezone.make_aware(datetime.combine(today, time.max), tz),
        )

    def test_profit_comes_out_net_of_tax(self):
        """
        Two units at 116 inclusive of 16%: revenue 232, tax 32, net 200, cost
        100, profit 100. A profit figure that counted the VAT would read 132 —
        wrong in the direction that gets a business into trouble.
        """
        self.sell("2")
        start, end = self.window()
        figures = reports.overview(self.account, start, end)

        self.assertEqual(figures["revenue"], Decimal("232.00"))
        self.assertEqual(figures["tax_collected"], Decimal("32.00"))
        self.assertEqual(figures["cost_of_sales"], Decimal("100.00"))
        self.assertEqual(figures["gross_profit"], Decimal("100.00"))
        self.assertEqual(figures["transactions"], 1)

    def test_another_shops_takings_are_not_in_the_figures(self):
        self.sell("2")
        services.checkout(
            shift=self.b_shift,
            cashier=self.b_staff,
            lines=[{"product": self.b_product, "quantity": Decimal("5")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("500.00")}],
        )

        start, end = self.window()
        figures = reports.overview(self.account, start, end)
        # Shop B's 500 must be nowhere in it.
        self.assertEqual(figures["revenue"], Decimal("232.00"))
        self.assertEqual(figures["transactions"], 1)

        branches = reports.by_branch(self.account, start, end)
        self.assertEqual([b["branch"] for b in branches], [self.a_branch.pk])

    def test_the_branch_comparison_carries_profit_and_refunds(self):
        """
        ══════════════════════════════════════════════════════════════════════
        TURNOVER ALONE ANSWERS THE WRONG QUESTION.

        Two units at 116 inclusive of 16%, costing 50 each: revenue 232, net
        200, profit 100. A comparison ranked on the 232 puts a busy
        low-margin branch above a quieter one that actually makes money —
        and where to put stock, staff and attention is the decision somebody
        opens a branch comparison to make.

        Refunds sit beside revenue for the reason the module docstring gives
        about the overview: a branch with a returns problem must not read the
        same as one without.
        ══════════════════════════════════════════════════════════════════════
        """
        self.sell("2")
        start, end = self.window()

        row = reports.by_branch(self.account, start, end)[0]
        self.assertEqual(row["branch"], self.a_branch.pk)
        self.assertEqual(row["revenue"], Decimal("232.00"))
        self.assertEqual(row["net_revenue"], Decimal("200.00"), "tax taken out")
        self.assertEqual(row["gross_profit"], Decimal("100.00"))
        self.assertEqual(row["refunded"], Decimal("0.00"))
        self.assertEqual(row["refunds"], 0)

    def test_a_two_line_sale_does_not_double_its_own_revenue(self):
        """
        ⚠ THE FAILURE A SINGLE QUERY WOULD HAVE CAUSED.

        Profit is aggregated over SaleItem and revenue over Sale. Annotating
        both in one values() joins the items, which multiplies the sale row
        once per line — and `Sum("total")` over a multiplied join counts a
        two-line sale's total twice. That is the classic way a revenue figure
        silently doubles, so the two aggregates are taken apart and stitched
        by branch id.
        """
        second = a_product(self.a_org, name="Bread", price="50.00", cost="20.00")
        stock(self.a_branch, second, "100")
        services.checkout(
            shift=self.a_shift,
            cashier=self.a_staff,
            lines=[
                {"product": self.product, "quantity": Decimal("1")},
                {"product": second, "quantity": Decimal("1")},
            ],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("166.00")}],
        )

        start, end = self.window()
        row = reports.by_branch(self.account, start, end)[0]
        self.assertEqual(row["revenue"], Decimal("166.00"), "not 332")
        self.assertEqual(row["transactions"], 1)

    def test_a_branch_that_only_refunded_is_still_in_the_comparison(self):
        """
        The comparison is built from completed SALES, so a branch whose only
        activity in the window was giving money back would be absent from it
        — the one branch most worth looking at, missing. It happens on any
        short window: a return taken on Monday against Saturday's sale.
        """
        sale = self.sell("2")
        start, end = self.window()

        # Move the sale out of the window and leave the refund inside it.
        Sale.objects.filter(pk=sale.pk).update(
            completed_at=start - timedelta(days=3)
        )
        services.refund_sale(
            sale=sale,
            branch=self.a_branch,
            processed_by=self.a_staff,
            lines=[{"sale_item": sale.items.get(), "quantity": Decimal("1")}],
            reason="Returned",
            method=Payment.Method.CASH,
        )

        rows = reports.by_branch(self.account, start, end)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["branch"], self.a_branch.pk)
        self.assertEqual(rows[0]["branch_name"], self.a_branch.branch_name)
        self.assertEqual(rows[0]["revenue"], Decimal("0.00"))
        self.assertEqual(rows[0]["refunded"], Decimal("116.00"))
        self.assertEqual(rows[0]["refunds"], 1)

    def test_another_shops_refund_does_not_add_a_branch_to_the_comparison(self):
        """
        The appended rows are the one place this function reaches outside the
        sales it was given, so the scoping is asserted on that path too.
        """
        b_sale = services.checkout(
            shift=self.b_shift,
            cashier=self.b_staff,
            lines=[{"product": self.b_product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("100.00")}],
        )
        services.refund_sale(
            sale=b_sale,
            branch=self.b_branch,
            processed_by=self.b_staff,
            lines=[{"sale_item": b_sale.items.get(), "quantity": Decimal("1")}],
            reason="Returned",
            method=Payment.Method.CASH,
        )

        self.sell("2")
        start, end = self.window()

        rows = reports.by_branch(self.account, start, end)
        self.assertEqual([row["branch"] for row in rows], [self.a_branch.pk])

    def test_a_voided_sale_is_not_revenue(self):
        sale = self.sell("2")
        services.void_sale(sale, reason="Rung up twice")

        start, end = self.window()
        figures = reports.overview(self.account, start, end)
        self.assertEqual(figures["revenue"], Decimal("0.00"))
        self.assertEqual(figures["transactions"], 0)

    def test_refunds_are_reported_beside_revenue_not_netted_off_it(self):
        """
        "We sold 232 and gave back 116" and "we sold 116" are different facts
        about a business, and the second hides a returns problem.
        """
        sale = self.sell("2")
        services.refund_sale(
            sale=sale,
            branch=self.a_branch,
            processed_by=self.a_staff,
            lines=[{"sale_item": sale.items.get(), "quantity": Decimal("1")}],
            reason="Returned",
            method=Payment.Method.CASH,
        )

        start, end = self.window()
        figures = reports.overview(self.account, start, end)
        self.assertEqual(figures["revenue"], Decimal("232.00"))
        self.assertEqual(figures["refunded"], Decimal("116.00"))
        self.assertEqual(figures["refunds"], 1)

    def test_an_empty_day_is_an_answer_not_a_division_error(self):
        start, end = self.window()
        figures = reports.overview(self.account, start, end)
        self.assertEqual(figures["transactions"], 0)
        self.assertEqual(figures["average_basket"], Decimal("0.00"))

    def test_stock_alerts_fire_at_the_reorder_level_not_below_it(self):
        """
        A reorder level of ten means "reorder when you reach ten". A strict
        comparison waits for nine, which is one sale later than asked.
        """
        row = BranchInventory.objects.get(branch=self.a_branch, product=self.product)
        row.quantity = Decimal("10")
        row.reorder_level = Decimal("10")
        row.save()

        alerts = reports.stock_alerts(self.account)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["product"], self.product.pk)
        self.assertFalse(alerts[0]["out_of_stock"])

    def test_stock_alerts_do_not_cross_tenants(self):
        for row in BranchInventory.objects.all():
            row.quantity = Decimal("0")
            row.reorder_level = Decimal("5")
            row.save()

        alerts = reports.stock_alerts(self.account)
        self.assertEqual({a["branch"] for a in alerts}, {self.a_branch.pk})
        self.assertTrue(all(a["out_of_stock"] for a in alerts))

    def test_register_status_shows_what_should_be_in_the_drawer(self):
        """
        Opening float, plus what was handed over, less what was handed back.

        ⚠ THIS TEST ASSERTED THE BUG, AND ITS DOCSTRING STATED THE WRONG
          FORMULA AS THOUGH IT WERE THE RULE.

          A 200 note against a 116 sale leaves the drawer 116 heavier, so a
          till opening with 1,000 should expect 1,116. It asserted 1,032 —
          1,000 + 116 − 84 — because `Payment.amount` is ALREADY net of
          change and `drawer()` subtracted the change from it a second time.
          See the banner in branches/services.py: the effect was every drawer
          in the product reading over by the day's change.

          Written down as the expected answer, a wrong figure stops being a
          bug and becomes a specification. `cash_taken` is now the 200 that
          crossed the counter, which is also what the column of that name
          means on every screen that shows it.
        """
        services.checkout(
            shift=self.a_shift,
            cashier=self.a_staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
        )
        status_rows = reports.register_status(self.account)
        self.assertEqual(len(status_rows), 1)
        row = status_rows[0]
        self.assertEqual(row["opening_cash"], Decimal("1000.00"))
        self.assertEqual(row["cash_taken"], Decimal("200.00"), "handed over")
        self.assertEqual(row["change_given"], Decimal("84.00"), "handed back")
        self.assertEqual(row["expected_cash"], Decimal("1116.00"))

    def test_naming_another_shops_branch_reports_nothing(self):
        """
        §8: a branch id from a client is not authority. Narrowing by a branch
        outside the caller's scope matches nothing, because the aggregate is
        taken over already-scoped sales.
        """
        self.sell("2")
        services.checkout(
            shift=self.b_shift,
            cashier=self.b_staff,
            lines=[{"product": self.b_product, "quantity": Decimal("5")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("500.00")}],
        )

        start, end = self.window()
        figures = reports.overview(self.account, start, end, self.b_branch.pk)
        self.assertEqual(figures["revenue"], Decimal("0.00"))


class ReportApiTests(TestCase):
    def setUp(self):
        self.org, self.branch, self.staff, _, self.shift = a_shop("Shop A")
        self.product = a_product(self.org, price="100.00", cost="60.00")
        stock(self.branch, self.product, "50")
        services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("3")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("300.00")}],
        )
        self.account = PlatformAccount.objects.create(
            genmars_account_id=701, email="owner@a.co.ke", full_name="A Owner"
        )
        TenantMembership.objects.create(account=self.account, organization=self.org)

    def sign_in(self):
        from identity.authentication import SUBSCRIBER_SESSION_KEY

        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()

    def test_every_report_refuses_an_anonymous_caller(self):
        for path in (
            "overview", "by-branch", "by-product", "by-cashier",
            "by-payment-method", "stock-alerts", "register-status",
            "drawers",
        ):
            response = self.client.get(f"/sls/reports/{path}/")
            self.assertIn(
                response.status_code, (401, 403), f"/sls/reports/{path}/ was open"
            )

    def test_the_overview_answers_for_a_signed_in_subscriber(self):
        self.sign_in()
        response = self.client.get("/sls/reports/overview/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["revenue"], "300.00")
        self.assertEqual(body["gross_profit"], "120.00")
        self.assertEqual(body["transactions"], 1)

    def test_a_malformed_date_falls_back_rather_than_500ing(self):
        self.sign_in()
        response = self.client.get("/sls/reports/overview/?from=not-a-date&to=nonsense")
        self.assertEqual(response.status_code, 200)

    def test_the_product_report_ranks_what_sold(self):
        self.sign_in()
        response = self.client.get("/sls/reports/by-product/")
        self.assertEqual(response.status_code, 200)
        products = response.json()["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["quantity"], "3.00")
        self.assertEqual(products[0]["revenue"], "300.00")


class ReportingWindowTests(TestCase):
    """
    What "today" means, and who gets to decide.

    ══════════════════════════════════════════════════════════════════════════
    THE BUG THIS PINS SHOWS FOR THREE HOURS A NIGHT AND LOOKS LIKE A QUIET DAY.

    The dashboard used to work its own dates out. The Next server renders in a
    container on UTC; the shop is in Nairobi, three hours ahead. So between
    midnight and 03:00 local it asked for yesterday, and a day that had taken
    302.00 reported 0.00 — which reads as a shop that sold nothing, not as a
    wrong question.

    So a period is NAMED and resolved against the clock the sales were stamped
    with. These tests are in shop time because `TIME_ZONE` is Africa/Nairobi
    and that is the whole point.
    ══════════════════════════════════════════════════════════════════════════
    """

    def window(self, **params):
        from django.test import RequestFactory

        from sales.reports import parse_window
        from rest_framework.request import Request

        request = Request(RequestFactory().get("/", params))
        start, end = parse_window(request)
        return (
            timezone.localtime(start).date().isoformat(),
            timezone.localtime(end).date().isoformat(),
        )

    def test_today_is_the_shops_today(self):
        today = timezone.localtime().date().isoformat()
        self.assertEqual(self.window(range="today"), (today, today))

    def test_a_week_is_seven_days_including_today(self):
        today = timezone.localtime().date()
        start, end = self.window(range="week")
        self.assertEqual(end, today.isoformat())
        self.assertEqual(
            start, (today - timedelta(days=6)).isoformat(), "six back plus today"
        )

    def test_a_month_starts_on_the_first(self):
        today = timezone.localtime().date()
        start, end = self.window(range="month")
        self.assertEqual(start, today.replace(day=1).isoformat())
        self.assertEqual(end, today.isoformat())

    def test_explicit_dates_are_still_honoured(self):
        """The custom window has not been taken away by the named ones."""
        self.assertEqual(
            self.window(**{"from": "2026-01-05", "to": "2026-02-09"}),
            ("2026-01-05", "2026-02-09"),
        )

    def test_a_name_wins_over_stray_dates(self):
        """
        Two answers to one question is worse than either. A named range that
        quietly took half of a `from` would produce a window nobody asked for.
        """
        today = timezone.localtime().date().isoformat()
        self.assertEqual(
            self.window(range="today", **{"from": "2020-01-01"}), (today, today)
        )

    def test_an_unknown_name_falls_back_to_today_rather_than_to_everything(self):
        """
        A typo must not widen the window. Falling open here would put a
        year of another period's figures on a screen somebody reads as today.
        """
        today = timezone.localtime().date().isoformat()
        self.assertEqual(self.window(range="fortnight"), (today, today))

    def test_the_window_covers_the_whole_last_day(self):
        """
        `to=the 30th` includes a sale at 23:59 on the 30th. An off-by-one here
        drops the last day of every month-end report, and month-end is when
        somebody actually reads one.
        """
        from django.test import RequestFactory

        from rest_framework.request import Request

        from sales.reports import parse_window

        request = Request(
            RequestFactory().get("/", {"from": "2026-09-30", "to": "2026-09-30"})
        )
        start, end = parse_window(request)
        self.assertEqual(timezone.localtime(start).hour, 0)
        self.assertEqual(timezone.localtime(end).hour, 23)
        self.assertEqual(timezone.localtime(end).minute, 59)


class BranchConfinedReportingTests(TestCase):
    """
    A branch manager's reports must stop at their branch.

    §5: a branch manager "should see only the data and actions permitted for
    that branch", and `ReportViewSet` chooses REPORTS_BRANCH over
    REPORTS_ORGANISATION for exactly that reason. Choosing the narrower
    permission is only half of it — the aggregate underneath has to be narrow
    too, and these tests are the half that was missing.
    """

    class _Session:
        """The two attributes StaffPrincipal reads. Signing in is not the
        thing under test, and a real session would need a request."""

        def __init__(self, credential):
            self.credential = credential

    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Two Branch Grocers")
        self.west = Branches.objects.create(
            organization=self.org, branch_name="Westlands",
            branch_location="Nairobi", branch_allocation="Shop 4",
            branch_manager="W Manager", is_active=True,
        )
        self.karen = Branches.objects.create(
            organization=self.org, branch_name="Karen",
            branch_location="Nairobi", branch_allocation="Shop 9",
            branch_manager="K Manager", is_active=True,
        )

        self.product = a_product(self.org, name="Milk", price="100.00", cost="60.00")
        stock(self.west, self.product, "100")
        stock(self.karen, self.product, "100")

        self.manager = OrganizationStaff.objects.create(
            organization=self.org, full_name="Grace Manager",
            email="grace@grocers.co.ke", phone_number="+254700000777",
            address="Nairobi", id_number=77001,
        )
        staffAssignment.objects.create(
            staff_member=self.manager, branch=self.west, staff_assignment="AM"
        )
        credential = StaffCredential(staff=self.manager, username="grace")
        credential.set_password("not-a-real-password")
        credential.save()
        self.principal = StaffPrincipal(self._Session(credential))

        # One sale at each branch, so a leak is unmistakable: 100 is correct
        # and 900 is the whole organisation.
        self.sell(self.west, "1")
        self.sell(self.karen, "8")

    def sell(self, branch, quantity):
        register = Register.objects.create(
            branch=branch, name=f"{branch.branch_name} till", register_number="T1"
        )
        shift = RegisterShift.objects.create(
            register=register, operator=self.manager, opening_cash=Decimal("0.00")
        )
        return services.checkout(
            shift=shift,
            cashier=self.manager,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            payments=[
                {
                    "method": Payment.Method.CASH,
                    "amount": Decimal(quantity) * Decimal("100.00"),
                }
            ],
        )

    def window(self):
        today = timezone.localtime().date()
        tz = timezone.get_current_timezone()
        return (
            timezone.make_aware(datetime.combine(today, time.min), tz),
            timezone.make_aware(datetime.combine(today, time.max), tz),
        )

    def test_the_overview_stops_at_the_branches_they_are_assigned_to(self):
        start, end = self.window()
        figures = reports.overview(self.principal, start, end)
        self.assertEqual(figures["revenue"], Decimal("100.00"))
        self.assertEqual(figures["transactions"], 1)

    def test_the_branch_comparison_lists_only_their_own_branch(self):
        """
        The one that makes the leak legible: a branch manager reading a table
        of every branch's takings is being shown the comparison §5 reserves
        for the organisation's own dashboard.
        """
        start, end = self.window()
        rows = reports.by_branch(self.principal, start, end)
        self.assertEqual([row["branch"] for row in rows], [self.west.pk])

    def test_naming_a_branch_they_are_not_assigned_to_reports_nothing(self):
        start, end = self.window()
        figures = reports.overview(self.principal, start, end, self.karen.pk)
        self.assertEqual(figures["revenue"], Decimal("0.00"))

    def test_the_cashier_report_does_not_reach_another_branch(self):
        start, end = self.window()
        rows = reports.by_cashier(self.principal, start, end)
        self.assertEqual([row["revenue"] for row in rows], [Decimal("100.00")])

    def test_an_owner_still_sees_the_whole_organisation(self):
        """The confinement must not catch a subscriber: `branch_scope`
        returns None for organisation-wide authority, and None means
        unrestricted, not none."""
        account = PlatformAccount.objects.create(
            genmars_account_id=777, email="owner@grocers.co.ke", full_name="Owner"
        )
        TenantMembership.objects.create(account=account, organization=self.org)
        start, end = self.window()
        figures = reports.overview(account, start, end)
        self.assertEqual(figures["revenue"], Decimal("900.00"))


class WhatIsOnTheReceiptTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════════
    A RECEIPT NAMES THE SALE'S OWN PEOPLE, NOT WHOEVER IS SIGNED IN NOW.

    The till drew its receipt from the terminal's session — the shop from
    the login, the cashier from whoever was at the keyboard. That is right
    exactly once, at the moment of the sale, and wrong on every REPRINT: a
    receipt reprinted tomorrow by a different cashier at a different branch
    would have carried today's names onto yesterday's transaction, which is
    the signature a duplicate-receipt refund is spotted by.

    So the names come off the sale, through the serializer, and these
    assertions are what stop them drifting back to the session.
    ══════════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        self.org, self.branch, self.staff, self.register, self.shift = a_shop(
            "Jamii Supermarket"
        )
        self.product = a_product(self.org, name="Yoghurt", price="63.00")
        stock(self.branch, self.product)

        self.customer = Customer.objects.create(
            organization=self.org,
            full_name="Mary Otieno",
            phone_number="+254748016528",
        )
        self.sale = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            customer=self.customer,
            lines=[{"product": self.product, "quantity": Decimal("3")}],
            # The cash actually handed over. `checkout` stores `amount` NET
            # of change and derives `tendered`/`change_given` from it — see
            # the note beside the Payment write in services.checkout.
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("200.00")}],
        )

    def shown(self):
        from .serializers import SaleSerializer

        return SaleSerializer(
            Sale.objects.select_related(
                "organization", "branch", "register", "cashier", "customer",
                "receipt",
            ).get(pk=self.sale.pk)
        ).data

    def test_it_carries_the_shop_the_branch_and_the_till(self):
        data = self.shown()

        self.assertEqual(data["organisation_name"], "Jamii Supermarket")
        self.assertEqual(data["branch_name"], "Jamii Supermarket Main")
        self.assertEqual(data["branch_location"], "Nairobi")
        self.assertEqual(data["register_name"], "Till 1")

    def test_it_names_the_cashier_who_rang_it_up(self):
        self.assertEqual(self.shown()["cashier_name"], "Jane Cashier")

    def test_it_names_the_customer_when_there_is_one(self):
        data = self.shown()

        self.assertEqual(data["customer_name"], "Mary Otieno")
        self.assertEqual(data["customer_phone"], "+254748016528")

    def test_a_walk_in_has_no_customer_rather_than_a_blank_one(self):
        """
        Most supermarket sales are to nobody in particular. The field has to
        come back empty so the slip omits the line — a receipt addressed to
        an empty name is worse than one addressed to nobody.
        """
        walk_in = services.checkout(
            shift=self.shift,
            cashier=self.staff,
            lines=[{"product": self.product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.CASH, "amount": Decimal("63.00")}],
        )
        from .serializers import SaleSerializer

        data = SaleSerializer(walk_in).data
        self.assertEqual(data["customer_name"], "")
        self.assertEqual(data["customer_phone"], "")

    def test_the_receipt_number_and_the_figures_a_customer_checks(self):
        data = self.shown()

        self.assertEqual(
            data["receipt"]["number"], f"{self.branch.branch_number}-{self.sale.number}"
        )
        line = data["items"][0]
        # The slip prints "3 × 63.00 … 189.00", so all three have to be there
        # and have to multiply out.
        self.assertEqual(line["unit_price"], "63.00")
        self.assertEqual(Decimal(line["quantity"]), Decimal("3"))
        self.assertEqual(line["line_total"], "189.00")

        paid = data["payments"][0]
        self.assertEqual(paid["method_label"], "Cash")
        self.assertEqual(paid["tendered"], "200.00")
        self.assertEqual(paid["change_given"], "11.00")
