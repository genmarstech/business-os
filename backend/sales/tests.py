"""
The till, tested where a bug costs somebody money.

Every test here is one where failure is a shop giving away stock, charging a
customer twice, or one tenant reading another's takings — not a cosmetic
defect. The arithmetic tests matter as much as the isolation ones: a VAT split
that is wrong by a cent is wrong on every receipt the business ever issues.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from branches.models import Branches, Register, RegisterShift
from catalog.models import CatalogCategories, CatalogCategoryProduct, TaxRule
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory, StockMovement
from organisations.models import BusinessOrganization, OrganizationStaff

from . import services
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

    def refund(self, quantity="1", **kwargs):
        return services.refund_sale(
            sale=self.sale,
            branch=self.branch,
            processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal(quantity),
                    **kwargs}],
            reason="Customer returned it",
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
            )

    def test_a_refund_is_retry_safe(self):
        first = services.refund_sale(
            sale=self.sale, branch=self.branch, processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
            reason="Returned", idempotency_key="till-1-rf-0001",
        )
        second = services.refund_sale(
            sale=self.sale, branch=self.branch, processed_by=self.staff,
            lines=[{"sale_item": self.line, "quantity": Decimal("1")}],
            reason="Returned", idempotency_key="till-1-rf-0001",
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
