"""
The callback is public. These are the tests that make that survivable.

If `test_a_forged_callback_cannot_mark_a_push_paid` ever goes red, the
endpoint has become a way to walk out of a shop with the stock.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from payments import services
from payments.models import StkPush, token_digest

from .factories import a_shop, an_mpesa_till

KEY = Fernet.generate_key().decode()


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class CallbackTests(TestCase):
    def setUp(self):
        self.org, (self.branch,) = a_shop()
        self.till = an_mpesa_till(self.org)

        with patch("payments.daraja.stk_push") as push:
            push.return_value = {
                "MerchantRequestID": "m-1",
                "CheckoutRequestID": "ws_CO_1",
            }
            self.push = services.request(
                branch=self.branch,
                amount=Decimal("150.00"),
                phone="0712345678",
                callback_base="https://business.genmars.co.ke",
            )
        # The token exists only in the URL we sent. Recover it the way the
        # service did, so the test can post as Safaricom would.
        self.token = self._token_for(self.push)

    def _token_for(self, push):
        """
        Brute-force recovery is impossible by design, so the test mints a
        known token and rewrites the digest — the same thing from the
        endpoint's point of view.
        """
        token = StkPush.new_token()
        push.callback_token_digest = token_digest(token)
        push.save(update_fields=["callback_token_digest"])
        return token

    def post_callback(self, token, body=None):
        return self.client.post(
            f"/pay/mpesa/callback/{token}",
            json.dumps(body or {}),
            content_type="application/json",
        )

    def test_a_forged_callback_cannot_mark_a_push_paid(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE ONE THAT MATTERS.

        A stranger posts a perfectly-formed success callback, with a valid
        token, while Safaricom says the customer has not paid. The push must
        stay unpaid: the body is never read and the query is the authority.
        ══════════════════════════════════════════════════════════════════
        """
        forged = {
            "Body": {
                "stkCallback": {
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 150},
                            {"Name": "MpesaReceiptNumber", "Value": "FORGED123"},
                        ]
                    },
                }
            }
        }
        with patch("payments.daraja.stk_query") as query:
            query.return_value = {"ResultCode": "1032", "ResultDesc": "Cancelled"}
            response = self.post_callback(self.token, forged)

        self.assertEqual(response.status_code, 200)
        self.push.refresh_from_db()
        self.assertEqual(self.push.status, StkPush.Status.FAILED)
        self.assertEqual(self.push.mpesa_receipt, "")

    def test_the_query_is_what_marks_a_push_paid(self):
        """The control: the same callback, with Safaricom agreeing."""
        with patch("payments.daraja.stk_query") as query:
            query.return_value = {"ResultCode": "0", "ResultDesc": "Success"}
            self.post_callback(self.token, {})

        self.push.refresh_from_db()
        self.assertEqual(self.push.status, StkPush.Status.PAID)

    def test_an_unknown_token_is_answered_normally(self):
        """
        200 and Daraja's acknowledgement shape, not 404.

        A 404 would tell whoever is probing which tokens are real, and
        Safaricom retry anything that is not a 200 — so a refusal would also
        mean being retried for hours.
        """
        with patch("payments.daraja.stk_query") as query:
            response = self.post_callback("not-a-real-token", {})
            query.assert_not_called()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ResultCode"], 0)
        self.push.refresh_from_db()
        self.assertEqual(self.push.status, StkPush.Status.REQUESTED)

    def test_the_callback_needs_no_session(self):
        """Safaricom cannot hold one. If this starts 403ing, nothing settles."""
        with patch("payments.daraja.stk_query") as query:
            query.return_value = {"ResultCode": "0", "ResultDesc": "Success"}
            response = self.post_callback(self.token, {})
        self.assertEqual(response.status_code, 200)

    def test_a_callback_that_explodes_still_answers_200(self):
        """
        Safaricom retry a non-200 for hours. The till's polling is the
        backstop, so a failure here must be quiet rather than loud.
        """
        with patch("payments.daraja.stk_query", side_effect=RuntimeError("boom")):
            response = self.post_callback(self.token, {})
        self.assertEqual(response.status_code, 200)


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class CredentialStorageTests(TestCase):
    def setUp(self):
        self.org, _ = a_shop()

    def test_the_secrets_are_not_in_the_database_in_the_clear(self):
        till = an_mpesa_till(self.org)
        till.refresh_from_db()
        for sealed in (
            till.consumer_key_sealed,
            till.consumer_secret_sealed,
            till.passkey_sealed,
        ):
            self.assertTrue(sealed)
            self.assertNotIn("not-a-real", sealed)

    def test_they_come_back_out_intact(self):
        till = an_mpesa_till(self.org)
        till.refresh_from_db()
        self.assertEqual(till.credentials()["passkey"], "not-a-real-passkey")

    def test_a_different_key_cannot_read_them(self):
        """
        The reason MPESA_CREDENTIAL_KEY is its own variable: rotating
        DJANGO_SECRET_KEY must not turn every tenant's till into noise.
        """
        till = an_mpesa_till(self.org)
        till.refresh_from_db()
        from payments.crypto import NotConfigured

        with override_settings(MPESA_CREDENTIAL_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(NotConfigured):
                till.credentials()

    def test_with_no_key_nothing_is_stored_in_the_clear(self):
        from payments.crypto import NotConfigured

        with override_settings(MPESA_CREDENTIAL_KEY=""):
            with self.assertRaises(NotConfigured):
                an_mpesa_till(self.org)
