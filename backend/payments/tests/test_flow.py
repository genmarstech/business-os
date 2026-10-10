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


class WhichShortCodeGoesWhereTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════════
    A PAYBILL IS ONE NUMBER AND A BUY-GOODS TILL IS TWO.

    Daraja asks for `BusinessShortCode`, `PartyB` and a password, and for a
    paybill all three are built from the same shortcode. For buy goods they
    are not: the store (head office) number identifies the merchant and is
    what the passkey was issued against, and the till number is what gets
    paid.

    This whole class exists because sending the till number for all three is
    answered with "Merchant does not exist" — a message that sends whoever
    reads it to the credentials, which were never wrong. The sandbox
    shortcode 174379 is a paybill, so every test here predating this one
    passed against the broken payload.
    ══════════════════════════════════════════════════════════════════════════
    """

    def sent(self, till, *, amount=150):
        """The body `stk_push` would put on the wire, with nothing real called."""
        with patch("payments.daraja._request") as request, \
                patch("payments.daraja.access_token", return_value="tok"):
            request.return_value = {"CheckoutRequestID": "x"}
            daraja.stk_push(
                till,
                amount=amount,
                phone="254700000000",
                callback_url="https://example.test/pay/mpesa/callback/t",
                reference="SHOP",
                description="Payment",
            )
        return request.call_args.kwargs["body"]

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_a_paybill_uses_one_number_for_everything(self):
        org, _ = a_shop()
        till = an_mpesa_till(org, short_code="174379")

        body = self.sent(till)

        self.assertEqual(body["BusinessShortCode"], "174379")
        self.assertEqual(body["PartyB"], "174379")
        self.assertEqual(body["TransactionType"], "CustomerPayBillOnline")

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_buy_goods_identifies_by_store_and_pays_the_till(self):
        org, _ = a_shop()
        till = an_mpesa_till(
            org,
            transaction_type="CustomerBuyGoodsOnline",
            short_code="5820101",     # the till the money lands in
            store_number="4109108",   # the merchant Safaricom knows
        )

        body = self.sent(till)

        self.assertEqual(
            body["BusinessShortCode"], "4109108",
            "The store number identifies the merchant. Sending the till "
            "number here is what Daraja answers with 'Merchant does not "
            "exist'.",
        )
        self.assertEqual(
            body["PartyB"], "5820101",
            "The money is paid into the till number, not the head office.",
        )

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_the_password_is_built_from_the_number_the_passkey_belongs_to(self):
        """
        The password is base64(shortcode + passkey + timestamp), and
        Safaricom verify it against the shortcode they were handed. Build it
        from the till number while claiming to be the store and the two
        disagree — which is NOT reported as a bad password.
        """
        import base64

        org, _ = a_shop()
        till = an_mpesa_till(
            org,
            transaction_type="CustomerBuyGoodsOnline",
            short_code="5820101",
            store_number="4109108",
        )

        body = self.sent(till)
        decoded = base64.b64decode(body["Password"]).decode()

        self.assertTrue(
            decoded.startswith("4109108"),
            f"The password must hash the store number, got {decoded[:7]!r}",
        )

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_a_blank_store_number_falls_back_rather_than_sending_nothing(self):
        """
        Every row written before `store_number` existed has a blank one, and
        an empty `BusinessShortCode` is a worse failure than a guess — the
        fallback is also exactly right for a shop whose two numbers match.
        """
        org, _ = a_shop()
        till = an_mpesa_till(
            org, transaction_type="CustomerBuyGoodsOnline", short_code="5820101"
        )

        body = self.sent(till)

        self.assertEqual(body["BusinessShortCode"], "5820101")
        self.assertEqual(body["PartyB"], "5820101")

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_the_query_asks_under_the_same_shortcode_the_push_went_out_as(self):
        """
        A push sent as the store number and queried as the till number asks
        Safaricom about a merchant that never sent it. The answer is a
        refusal, and the customer has already paid.
        """
        org, _ = a_shop()
        till = an_mpesa_till(
            org,
            transaction_type="CustomerBuyGoodsOnline",
            short_code="5820101",
            store_number="4109108",
        )

        pushed = self.sent(till)

        with patch("payments.daraja._request") as request, \
                patch("payments.daraja.access_token", return_value="tok"):
            request.return_value = {"ResultCode": "0"}
            daraja.stk_query(till, checkout_request_id="ws_CO_1")
            queried = request.call_args.kwargs["body"]

        self.assertEqual(queried["BusinessShortCode"], pushed["BusinessShortCode"])


class WhatSafaricomSaidTests(TestCase):
    """
    A refusal used to read "M-Pesa refused the request." and nothing else,
    which is what a shop owner saw in their notifications: a failed payment
    with no cause and nothing to do about it. Daraja's own `errorMessage` is
    usually the whole diagnosis.
    """

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_a_refusal_carries_safaricoms_own_words(self):
        import urllib.error

        org, (branch,) = a_shop()
        an_mpesa_till(org)

        refusal = urllib.error.HTTPError(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            400,
            "Bad Request",
            {},  # type: ignore[arg-type]
            None,
        )
        refusal.read = lambda: (  # type: ignore[method-assign]
            b'{"errorCode":"500.001.1001","errorMessage":"Merchant does not exist"}'
        )

        with patch("payments.daraja.access_token", return_value="tok"), \
                patch("urllib.request.urlopen", side_effect=refusal):
            with self.assertRaises(services.PaymentError) as refused:
                services.request(
                    branch=branch,
                    amount=Decimal("150.00"),
                    phone="0700000000",
                    callback_base="https://example.test",
                )

        self.assertIn("Merchant does not exist", str(refused.exception))

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_the_failed_push_records_the_same_sentence(self):
        """
        The row outlives the exception, and the notification is written from
        the row. A reason that reaches only the cashier's screen is a reason
        the owner never sees.
        """
        import urllib.error

        org, (branch,) = a_shop()
        an_mpesa_till(org)

        refusal = urllib.error.HTTPError(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            400, "Bad Request", {}, None,  # type: ignore[arg-type]
        )
        refusal.read = lambda: (  # type: ignore[method-assign]
            b'{"errorMessage":"Merchant does not exist"}'
        )

        with patch("payments.daraja.access_token", return_value="tok"), \
                patch("urllib.request.urlopen", side_effect=refusal):
            with self.assertRaises(services.PaymentError):
                services.request(
                    branch=branch,
                    amount=Decimal("150.00"),
                    phone="0700000000",
                    callback_base="https://example.test",
                )

        push = StkPush.objects.get()
        self.assertEqual(push.status, StkPush.Status.FAILED)
        self.assertIn("Merchant does not exist", push.result_description)


class WhenTheKeyItselfIsRefusedTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE DOOR AND THE TRANSACTION FAIL DIFFERENTLY, AND MUST SAY SO.

    Daraja refuses an unusable consumer key/secret at `/oauth/v1/generate`,
    before a shortcode has been mentioned — and frequently with an EMPTY
    BODY, so there is nothing of Safaricom's to quote. Reported as "M-Pesa
    refused the request." that is indistinguishable from a rejected push,
    and it sends whoever reads it to check the till number, which was never
    the problem.

    The overwhelmingly common cause is sandbox credentials pointed at
    production. They work perfectly in the sandbox, so every other signal
    says the credentials are fine.
    ══════════════════════════════════════════════════════════════════════════
    """

    def refused_token(self, *, environment, body=b""):
        import urllib.error

        org, (branch,) = a_shop()
        an_mpesa_till(org, environment=environment)

        refusal = urllib.error.HTTPError(
            f"{daraja.base_url(environment)}/oauth/v1/generate",
            400, "Bad Request", {}, None,  # type: ignore[arg-type]
        )
        refusal.read = lambda: body  # type: ignore[method-assign]

        with patch("urllib.request.urlopen", side_effect=refusal):
            with self.assertRaises(services.PaymentError) as refused:
                services.request(
                    branch=branch,
                    amount=Decimal("150.00"),
                    phone="0700000000",
                    callback_base="https://example.test",
                )
        return str(refused.exception)

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_it_names_the_credentials_not_the_payment(self):
        said = self.refused_token(environment="production")

        self.assertIn("consumer key", said)
        self.assertIn("production", said)
        self.assertNotIn(
            "refused the request", said,
            "A rejected key must not read like a rejected payment.",
        )

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_it_says_which_environment_it_tried(self):
        """
        The whole diagnosis is usually "these are the other environment's
        credentials", and the reader cannot make that deduction without
        being told which one was attempted.
        """
        self.assertIn("sandbox", self.refused_token(environment="sandbox"))

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_it_never_repeats_any_part_of_the_credential(self):
        from .factories import FAKE_KEY, FAKE_SECRET

        said = self.refused_token(environment="production")

        self.assertNotIn(FAKE_KEY, said)
        self.assertNotIn(FAKE_SECRET, said)

    @override_settings(MPESA_CREDENTIAL_KEY=KEY)
    def test_an_empty_bodied_refusal_still_carries_the_status(self):
        """
        Nothing to quote is not nothing to say. A bare sentence is what made
        the live failure unreadable.
        """
        import urllib.error

        org, (branch,) = a_shop()
        an_mpesa_till(org)

        refusal = urllib.error.HTTPError(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            503, "Service Unavailable", {}, None,  # type: ignore[arg-type]
        )
        refusal.read = lambda: b""  # type: ignore[method-assign]

        with patch("payments.daraja.access_token", return_value="tok"), \
                patch("urllib.request.urlopen", side_effect=refusal):
            with self.assertRaises(services.PaymentError) as refused:
                services.request(
                    branch=branch,
                    amount=Decimal("150.00"),
                    phone="0700000000",
                    callback_base="https://example.test",
                )

        self.assertIn("503", str(refused.exception))


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class NotRecognisedIsNotRefusedTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════════
    THE TEN-SECOND FAILURE THAT COST A REAL KSh 80.

    Production answered a query with `4999 — The transaction is still under
    processing`. That code was not in PENDING_CODES, so `confirm` fell
    through to its "anything else is a refusal" branch and settled the push
    FAILED ten seconds after it was sent, while the customer was still
    looking at the PIN prompt. `confirm` then returns a settled push
    untouched, so when the money arrived nothing ever asked again.

    The direction is now reversed: only a listed code is final.
    ══════════════════════════════════════════════════════════════════════════
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop()
        an_mpesa_till(self.org)

    def a_push(self):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {
                "MerchantRequestID": "m-1",
                "CheckoutRequestID": "ws_CO_1",
            }
            return services.request(
                branch=self.branch,
                amount=Decimal("80.00"),
                phone="0748016528",
                callback_base="https://business.genmars.co.ke",
            )

    def answering(self, push, answer):
        with patch("payments.daraja.stk_query", return_value=answer):
            return services.confirm(push)

    def test_still_under_processing_is_not_a_refusal(self):
        push = self.a_push()

        settled = self.answering(
            push,
            {"ResultCode": "4999",
             "ResultDesc": "The transaction is still under processing"},
        )

        self.assertEqual(
            settled.status, StkPush.Status.REQUESTED,
            "4999 means the customer has not answered yet. Marking it failed "
            "is what left a paid KSh 80 recorded as a failure.",
        )

    def test_a_code_nobody_has_ever_seen_is_also_not_a_refusal(self):
        """
        The rule is the default, not a list of exceptions to it. A code
        Safaricom introduce next year must not close somebody's payment.
        """
        push = self.a_push()

        settled = self.answering(push, {"ResultCode": "7777", "ResultDesc": "?"})

        self.assertEqual(settled.status, StkPush.Status.REQUESTED)

    def test_a_real_refusal_still_settles_at_once(self):
        """
        The reverse failure matters too: a cashier whose customer cancelled
        must be freed to take cash now, not in three minutes.
        """
        push = self.a_push()

        settled = self.answering(
            push, {"ResultCode": "1032", "ResultDesc": "Request Cancelled by user."}
        )

        self.assertEqual(settled.status, StkPush.Status.FAILED)
        self.assertEqual(
            settled.result_description, "The customer cancelled it.",
            "Said in words a cashier can read to the person in front of them.",
        )

    def test_an_unrecognised_code_expires_rather_than_waiting_for_ever(self):
        push = self.a_push()
        StkPush.objects.filter(pk=push.pk).update(
            created_at=timezone.now() - timedelta(seconds=services.EXPIRE_AFTER_SECONDS + 1)
        )
        push.refresh_from_db()

        settled = self.answering(push, {"ResultCode": "4999", "ResultDesc": "x"})

        self.assertEqual(settled.status, StkPush.Status.EXPIRED)

    def test_paid_is_still_paid(self):
        push = self.a_push()

        settled = self.answering(push, {"ResultCode": "0", "ResultDesc": "ok"})

        self.assertEqual(settled.status, StkPush.Status.PAID)


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class AskingAgainTests(TestCase):
    """
    A push closed unpaid has to be re-askable, or a wrongly-closed payment
    can only be repaired by editing the database.
    """

    def setUp(self):
        self.org, (self.branch,) = a_shop()
        an_mpesa_till(self.org)

    def a_failed_push(self):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "ws_CO_1"}
            made = services.request(
                branch=self.branch, amount=Decimal("80.00"),
                phone="0748016528", callback_base="https://b.test",
            )
        with patch("payments.daraja.stk_query",
                   return_value={"ResultCode": "1032", "ResultDesc": "no"}):
            return services.confirm(made)

    def test_a_payment_that_actually_went_through_can_be_recovered(self):
        push = self.a_failed_push()
        self.assertEqual(push.status, StkPush.Status.FAILED)

        with patch("payments.daraja.stk_query",
                   return_value={"ResultCode": "0", "ResultDesc": "ok"}):
            again = services.recheck(push)

        self.assertEqual(again.status, StkPush.Status.PAID)

    def test_it_asks_safaricom_rather_than_deciding(self):
        """A push that really failed settles failed again. This is not a
        way to turn a refusal into a payment."""
        push = self.a_failed_push()

        with patch("payments.daraja.stk_query",
                   return_value={"ResultCode": "1032", "ResultDesc": "no"}):
            again = services.recheck(push)

        self.assertEqual(again.status, StkPush.Status.FAILED)

    def test_a_paid_push_is_never_reopened(self):
        """
        Reopening a paid push would let a payment be detached from the sale
        it funded. It is the one answer that is not revisited.
        """
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "ws_CO_2"}
            made = services.request(
                branch=self.branch, amount=Decimal("80.00"),
                phone="0748016528", callback_base="https://b.test",
            )
        with patch("payments.daraja.stk_query",
                   return_value={"ResultCode": "0", "ResultDesc": "ok"}):
            paid = services.confirm(made)

        with patch("payments.daraja.stk_query") as asked:
            again = services.recheck(paid)

        asked.assert_not_called()
        self.assertEqual(again.status, StkPush.Status.PAID)

    def test_one_that_never_reached_safaricom_is_not_re_asked(self):
        with patch("payments.daraja.stk_push",
                   side_effect=daraja.DarajaError("nope")):
            with self.assertRaises(services.PaymentError):
                services.request(
                    branch=self.branch, amount=Decimal("80.00"),
                    phone="0748016528", callback_base="https://b.test",
                )

        push = StkPush.objects.get()
        with patch("payments.daraja.stk_query") as asked:
            services.recheck(push)

        asked.assert_not_called()
