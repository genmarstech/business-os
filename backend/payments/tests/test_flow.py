"""
Requesting a payment, confirming it, and spending it on exactly one sale.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from payments import daraja, services
from payments.models import StkPush
from sales import services as sales_services
from sales.models import Payment

from .factories import (
    a_product,
    a_shift,
    a_shop,
    a_staff,
    an_mpesa_till,
    assign,
    stocked,
)

KEY = Fernet.generate_key().decode()


class PhoneTests(TestCase):
    """
    A cashier types what they hear. All of these are the same phone, and all
    three have to work or the feature fails in a hurry at a counter.
    """

    def test_the_forms_a_cashier_might_type(self):
        for typed in ("0712345678", "+254 712 345 678", "254712345678",
                      "712345678", "0712 345678"):
            self.assertEqual(services.normalise_phone(typed), "254712345678")

    def test_airtel_is_a_phone_too(self):
        self.assertEqual(services.normalise_phone("0100000000"), "254100000000")

    def test_nonsense_is_refused_with_an_example(self):
        with self.assertRaises(services.PaymentError) as caught:
            services.normalise_phone("12345")
        self.assertIn("0712", str(caught.exception))


class AmountTests(TestCase):
    def test_a_whole_amount_passes(self):
        self.assertEqual(services.whole_shillings(Decimal("150.00")), 150)

    def test_cents_are_refused_rather_than_rounded(self):
        """
        ── NEITHER DIRECTION IS RIGHT ─────────────────────────────────────
        Rounding down leaves the drawer short on every such sale, which
        compounds and never reconciles. Rounding up charges a customer more
        than the receipt says. So the cashier is told to adjust the basket,
        and a shop that wants to round does it as a discount, on the record.
        """
        with self.assertRaises(services.PaymentError) as caught:
            services.whole_shillings(Decimal("150.50"))
        self.assertIn("150.50", str(caught.exception))

    def test_nothing_to_pay_is_refused(self):
        with self.assertRaises(services.PaymentError):
            services.whole_shillings(Decimal("0.00"))


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class RequestTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.till = an_mpesa_till(self.org)

    def request(self, **kwargs):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {
                "MerchantRequestID": "m-1",
                "CheckoutRequestID": "ws_CO_1",
            }
            self.sent = push
            return services.request(
                branch=self.branch,
                amount=kwargs.pop("amount", Decimal("150.00")),
                phone=kwargs.pop("phone", "0712345678"),
                callback_base="https://business.genmars.co.ke",
                **kwargs,
            )

    def test_a_push_is_written_before_safaricom_is_called(self):
        """
        So a response arriving while the process dies has somewhere to land,
        and so the callback token exists before the URL carrying it is given
        away.
        """
        push = self.request()
        self.assertEqual(push.status, StkPush.Status.REQUESTED)
        self.assertEqual(push.checkout_request_id, "ws_CO_1")
        self.assertEqual(push.phone_number, "254712345678")

    def test_the_callback_url_carries_an_unguessable_token(self):
        """
        A sequential id in the URL would be guessable, and guessing one is
        the whole attack the token exists to stop. Asserted by shape — long,
        and not the row's own identifiers — rather than by checking it does
        not contain "1", which a base64 token often will.
        """
        push = self.request()
        segment = self.sent.call_args.kwargs["callback_url"].rsplit("/", 1)[-1]
        self.assertIn("/pay/mpesa/callback/", self.sent.call_args.kwargs["callback_url"])
        self.assertGreater(len(segment), 30)
        self.assertNotEqual(segment, str(push.pk))
        self.assertNotEqual(segment, push.checkout_request_id)
        # And what is stored is the digest, not the token: a database read
        # must not hand anybody a working callback URL.
        self.assertNotEqual(push.callback_token_digest, segment)

    def test_a_refusal_from_safaricom_settles_the_push_as_failed(self):
        with patch("payments.daraja.stk_push",
                   side_effect=daraja.DarajaError("M-Pesa refused the request.")):
            with self.assertRaises(services.PaymentError):
                services.request(
                    branch=self.branch,
                    amount=Decimal("150.00"),
                    phone="0712345678",
                    callback_base="https://business.genmars.co.ke",
                )
        push = StkPush.objects.get()
        self.assertEqual(push.status, StkPush.Status.FAILED)

    def test_a_till_that_is_switched_off_refuses_before_calling_out(self):
        self.till.is_active = False
        self.till.save(update_fields=["is_active"])
        with patch("payments.daraja.stk_push") as push:
            with self.assertRaises(services.PaymentError):
                services.request(
                    branch=self.branch,
                    amount=Decimal("150.00"),
                    phone="0712345678",
                    callback_base="https://business.genmars.co.ke",
                )
            push.assert_not_called()


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class ConfirmTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.till = an_mpesa_till(self.org)
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c"}
            self.push = services.request(
                branch=self.branch,
                amount=Decimal("150.00"),
                phone="0712345678",
                callback_base="https://business.genmars.co.ke",
            )

    def confirm(self, answer):
        with patch("payments.daraja.stk_query", return_value=answer):
            return services.confirm(self.push)

    def test_still_processing_leaves_it_waiting(self):
        """
        Daraja answers an in-flight query with "being processed". Treating
        that as a refusal would mark a push failed while the customer is
        still looking at the PIN prompt.
        """
        settled = self.confirm({"ResultCode": "1032-pending", "ResultDesc": "..."})
        self.assertEqual(settled.status, StkPush.Status.REQUESTED)

    def test_an_unreachable_safaricom_is_not_the_same_as_unpaid(self):
        """
        Marking a push failed because OUR network blipped tells a cashier to
        take cash from a customer who has already paid.
        """
        with patch("payments.daraja.stk_query",
                   side_effect=daraja.DarajaError("unreachable")):
            settled = services.confirm(self.push)
        self.assertEqual(settled.status, StkPush.Status.REQUESTED)

    def test_it_gives_up_eventually(self):
        StkPush.objects.filter(pk=self.push.pk).update(
            created_at=timezone.now() - timedelta(seconds=400)
        )
        self.push.refresh_from_db()
        settled = self.confirm({"ResultCode": "1032-pending", "ResultDesc": "..."})
        self.assertEqual(settled.status, StkPush.Status.EXPIRED)

    def test_confirming_a_settled_push_does_not_ask_again(self):
        self.confirm({"ResultCode": "0", "ResultDesc": "Success"})
        with patch("payments.daraja.stk_query") as query:
            services.confirm(self.push)
            query.assert_not_called()


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class SpendBase(TestCase):
    """
    A shop that can both take a payment and ring up a sale.

    Split from the tests so `CheckoutSpendsThePushTests` can reuse the
    fixture without inheriting — and re-running — every assertion in
    `SpendTests`.
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.till = an_mpesa_till(self.org)
        self.cashier = a_staff(
            self.org, name="Jane", email="jane@a.co.ke", id_number=1001
        )
        assign(self.cashier, self.branch)
        self.shift = a_shift(self.branch, self.cashier)
        self.product = a_product(self.org, price="150.00")
        stocked(self.branch, self.product)

    def a_paid_push(self, amount="150.00"):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c"}
            created = services.request(
                branch=self.branch,
                amount=Decimal(amount),
                phone="0712345678",
                callback_base="https://business.genmars.co.ke",
            )
        with patch("payments.daraja.stk_query",
                   return_value={"ResultCode": "0", "ResultDesc": "Success"}):
            return services.confirm(created)

    def a_sale(self, quantity="1"):
        return sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            payments=[
                {
                    "method": Payment.Method.MPESA,
                    "amount": Decimal(quantity) * Decimal("150.00"),
                }
            ],
        )


class SpendTests(SpendBase):
    """A confirmed payment funds exactly one sale."""

    def test_a_confirmed_push_pays_for_a_sale(self):
        push = self.a_paid_push()
        sale = self.a_sale()
        services.spend(push, sale)
        push.refresh_from_db()
        self.assertEqual(push.sale, sale)

    def test_it_cannot_pay_for_a_second_one(self):
        """
        At a busy till this is a double-tap long before it is fraud, so the
        refusal is a sentence rather than an IntegrityError.
        """
        push = self.a_paid_push()
        services.spend(push, self.a_sale())
        with self.assertRaises(services.PaymentError) as caught:
            services.spend(push, self.a_sale())
        self.assertIn("already paid", str(caught.exception))

    def test_an_unconfirmed_push_pays_for_nothing(self):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c"}
            waiting = services.request(
                branch=self.branch, amount=Decimal("150.00"),
                phone="0712345678",
                callback_base="https://business.genmars.co.ke",
            )
        with self.assertRaises(services.PaymentError):
            services.spend(waiting, self.a_sale())

    def test_the_amounts_have_to_match(self):
        """
        Otherwise a 10/- push funds a 10,000/- basket, which is the simplest
        possible way to rob the shop.
        """
        push = self.a_paid_push("150.00")
        with self.assertRaises(services.PaymentError) as caught:
            services.spend(push, self.a_sale("2"))
        self.assertIn("300.00", str(caught.exception))

    def test_a_payment_cannot_cross_to_another_business(self):
        other, (other_branch,) = a_shop("Shop B")
        an_mpesa_till(other)
        push = self.a_paid_push()
        other_staff = a_staff(
            other, name="B", email="b@b.co.ke", id_number=2002
        )
        assign(other_staff, other_branch)
        other_product = a_product(other, name="B Milk", price="150.00")
        stocked(other_branch, other_product)
        other_sale = sales_services.checkout(
            shift=a_shift(other_branch, other_staff),
            cashier=other_staff,
            lines=[{"product": other_product, "quantity": Decimal("1")}],
            payments=[{"method": Payment.Method.MPESA, "amount": Decimal("150.00")}],
        )
        with self.assertRaises(services.PaymentError):
            services.spend(push, other_sale)


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class CheckoutSpendsThePushTests(SpendBase):
    """
    The link has to be made inside the sale's own transaction.

    Two requests — ring up the sale, then attach the payment — would leave a
    window in which the sale exists and the push is still unspent, and a
    confirmed payment sitting unspent is one that can fund a second basket.
    """

    def ring_up(self, push, quantity="1"):
        return sales_services.checkout(
            shift=self.shift,
            cashier=self.cashier,
            lines=[{"product": self.product, "quantity": Decimal(quantity)}],
            payments=[
                {
                    "method": Payment.Method.MPESA,
                    "amount": Decimal(quantity) * Decimal("150.00"),
                    "stk_push": push,
                }
            ],
        )

    def test_checkout_attaches_the_push(self):
        push = self.a_paid_push()
        sale = self.ring_up(push)
        push.refresh_from_db()
        self.assertEqual(push.sale, sale)
        self.assertEqual(sale.payments.get().method, Payment.Method.MPESA)

    def test_reusing_a_spent_push_rolls_the_whole_sale_back(self):
        """
        Not "the sale goes through and the link quietly fails". The stock
        must not move either, or the shop has given away goods against a
        payment that already paid for something else.
        """
        from sales.models import Sale

        push = self.a_paid_push()
        self.ring_up(push)
        before = Sale.objects.count()

        with self.assertRaises(services.PaymentError):
            self.ring_up(push)

        self.assertEqual(Sale.objects.count(), before)

    def test_a_mismatched_amount_rolls_the_sale_back(self):
        from sales.models import Sale

        push = self.a_paid_push("150.00")
        before = Sale.objects.count()
        with self.assertRaises(services.PaymentError):
            self.ring_up(push, "2")
        self.assertEqual(Sale.objects.count(), before)
