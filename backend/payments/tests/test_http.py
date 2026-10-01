"""
Who may configure M-Pesa, and who may take a payment with it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from identity import services as identity_services
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import TenantMembership
from payments.models import MpesaTill, StkPush

from .factories import (
    a_shop,
    a_staff,
    a_subscriber,
    a_till_credential,
    an_mpesa_till,
    assign,
)

KEY = Fernet.generate_key().decode()


@override_settings(MPESA_CREDENTIAL_KEY=KEY)
class Base(TestCase):
    def setUp(self):
        self.org, (self.west, self.karen) = a_shop(
            "Grocers", branches=("Westlands", "Karen")
        )

    def sign_in_owner(self, number=90):
        if not hasattr(self, "_owner"):
            self._owner = a_subscriber(
                self.org, TenantMembership.Role.OWNER, number=number
            )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self._owner.pk
        session.save()
        return self._owner

    def sign_in_cashier(self, branch=None, *, username="jane", id_number=1001):
        staff = a_staff(
            self.org, name="Jane", email=f"{username}@a.co.ke", id_number=id_number
        )
        assign(staff, branch or self.west)
        _, token = identity_services.open_staff_session(
            a_till_credential(staff, username)
        )
        self.token = token
        return staff

    def auth(self):
        return {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}

    def post(self, path, payload, **extra):
        return self.client.post(
            path, json.dumps(payload), content_type="application/json", **extra
        )


class ConfigurationTests(Base):
    def test_an_owner_can_set_it_up(self):
        self.sign_in_owner()
        response = self.post(
            "/pay/mpesa/till/",
            {
                "organization": self.org.pk,
                "short_code": "174379",
                "transaction_type": "CustomerPayBillOnline",
                "consumer_key": "k", "consumer_secret": "s", "passkey": "p",
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(MpesaTill.objects.get().is_complete)

    def test_the_secrets_never_come_back(self):
        """
        Not masked — absent. A field that returns four characters of a
        passkey returns them to anybody who gets a session.
        """
        self.sign_in_owner()
        an_mpesa_till(self.org)
        body = self.client.get("/pay/mpesa/till/").json()[0]

        self.assertNotIn("consumer_key", body)
        self.assertNotIn("passkey", body)
        self.assertNotIn("not-a-real", json.dumps(body))
        # But a settings screen can still say "entered".
        self.assertTrue(body["passkey_set"])

    def test_it_cannot_be_turned_on_half_configured(self):
        """
        "On but unusable" is a state a shop discovers at the counter with a
        customer waiting.
        """
        self.sign_in_owner()
        response = self.post(
            "/pay/mpesa/till/",
            {"organization": self.org.pk, "short_code": "174379", "is_active": True},
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("is_active", response.json())

    def test_a_blank_secret_leaves_the_stored_one_alone(self):
        """
        The form cannot show what is stored, so it submits blanks for
        anything untouched. Reading a blank as "delete the passkey" would
        break the till every time somebody fixed a typo in the short code.
        """
        self.sign_in_owner()
        till = an_mpesa_till(self.org)
        response = self.client.patch(
            f"/pay/mpesa/till/{till.pk}/",
            json.dumps({"short_code": "999999"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        till.refresh_from_db()
        self.assertEqual(till.short_code, "999999")
        self.assertEqual(till.credentials()["passkey"], "not-a-real-passkey")

    def test_a_cashier_cannot_read_or_write_the_configuration(self):
        """A till needs M-Pesa to work; it has no business reading how it is wired."""
        self.sign_in_cashier()
        self.assertEqual(
            self.client.get("/pay/mpesa/till/", **self.auth()).status_code, 403
        )
        self.assertEqual(
            self.post(
                "/pay/mpesa/till/",
                {"organization": self.org.pk, "short_code": "1"},
                **self.auth(),
            ).status_code,
            403,
        )

    def test_another_tenants_configuration_is_invisible(self):
        other, _ = a_shop("Shop B")
        an_mpesa_till(other)
        self.sign_in_owner()
        self.assertEqual(self.client.get("/pay/mpesa/till/").json(), [])

    @override_settings(MPESA_CREDENTIAL_KEY="")
    def test_with_no_platform_key_nothing_is_stored(self):
        """
        503 and a plain explanation, rather than storing a merchant secret
        in the clear.
        """
        self.sign_in_owner()
        response = self.post(
            "/pay/mpesa/till/",
            {"organization": self.org.pk, "short_code": "174379",
             "consumer_key": "k", "consumer_secret": "s", "passkey": "p"},
        )
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(MpesaTill.objects.count(), 0)


class RequestPaymentTests(Base):
    def setUp(self):
        super().setUp()
        an_mpesa_till(self.org)

    def ask(self, branch=None, **extra):
        with patch("payments.daraja.stk_push") as push:
            push.return_value = {"MerchantRequestID": "m", "CheckoutRequestID": "c"}
            self.sent = push
            return self.post(
                "/pay/mpesa/pushes/request/",
                {
                    "branch": (branch or self.west).pk,
                    "amount": "150.00",
                    "phone_number": "0712345678",
                },
                **extra,
            )

    def test_a_cashier_can_ask_for_a_payment(self):
        """The control. Every refusal below needs this to pass first."""
        self.sign_in_cashier()
        response = self.ask(**self.auth())
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["status"], "requested")

    def test_a_cashier_cannot_ask_at_a_branch_they_do_not_work_at(self):
        self.sign_in_cashier(self.west)
        response = self.ask(self.karen, **self.auth())
        self.assertEqual(response.status_code, 403, response.content)

    def test_another_tenants_branch_reads_as_not_existing(self):
        """
        ── THE HOLE THIS CLOSES ───────────────────────────────────────────
        `access.may(user, perm, branch_id)` is branch-agnostic for a
        SUBSCRIBER — `branch_scope` returns None for organisation-wide
        authority — so it would answer yes about a branch belonging to
        another business. The tenant check has to come first, and it is
        reported as invalid input, never as forbidden.
        """
        other, (their_branch,) = a_shop("Shop B")
        self.sign_in_owner()
        response = self.ask(their_branch)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("branch", response.json())
        self.assertEqual(StkPush.objects.count(), 0)

    def test_a_basket_with_cents_is_refused_with_a_reason(self):
        self.sign_in_cashier()
        with patch("payments.daraja.stk_push") as push:
            response = self.post(
                "/pay/mpesa/pushes/request/",
                {"branch": self.west.pk, "amount": "150.50",
                 "phone_number": "0712345678"},
                **self.auth(),
            )
            push.assert_not_called()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("150.50", json.dumps(response.json()))

    def test_checking_asks_safaricom_rather_than_reading_the_row(self):
        """
        Callbacks get lost. A till that could only learn the answer from one
        would hang every time that happened.
        """
        self.sign_in_cashier()
        push_id = self.ask(**self.auth()).json()["id"]

        with patch("payments.daraja.stk_query") as query:
            query.return_value = {"ResultCode": "0", "ResultDesc": "Success"}
            response = self.client.get(
                f"/pay/mpesa/pushes/{push_id}/check/", **self.auth()
            )
            query.assert_called_once()

        self.assertEqual(response.json()["status"], "paid")

    def test_another_tenant_cannot_check_a_push(self):
        self.sign_in_cashier()
        push_id = self.ask(**self.auth()).json()["id"]

        other, (other_branch,) = a_shop("Shop B")
        intruder = a_staff(other, name="X", email="x@b.co.ke", id_number=5005)
        assign(intruder, other_branch)
        _, token = identity_services.open_staff_session(
            a_till_credential(intruder, "x")
        )
        # 404, not 403 — a 403 confirms the row exists.
        self.assertEqual(
            self.client.get(
                f"/pay/mpesa/pushes/{push_id}/check/",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            ).status_code,
            404,
        )

    def test_a_push_cannot_be_patched_into_being_paid(self):
        self.sign_in_cashier()
        push_id = self.ask(**self.auth()).json()["id"]
        response = self.client.patch(
            f"/pay/mpesa/pushes/{push_id}/",
            json.dumps({"status": "paid"}),
            content_type="application/json",
            **self.auth(),
        )
        self.assertEqual(response.status_code, 405, response.content)
