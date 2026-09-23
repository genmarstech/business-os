"""
A cashier resetting a forgotten till password.

═══════════════════════════════════════════════════════════════════════════════
THREE PROPERTIES CARRY THIS FEATURE, AND EACH HAS A TEST THAT WOULD CATCH IT
BREAKING.

1. **It tells a stranger nothing.** The request endpoint answers identically
   whether the username exists, is deactivated, has no email, or is rate
   limited. A till sign-in screen is reachable by anyone who can reach the
   shop's URL, so a varying answer is a way to enumerate other people's staff.

2. **A code is a credential.** Hashed at rest, single use, expiring, attempt
   limited, and invalidated when a newer one is issued.

3. **It cannot be used to flood an inbox.** Every request sends mail, so the
   ceiling is what stops this being a way to use our sending domain against
   somebody else.

The fourth thing worth stating: a reset leaves `must_change_password` FALSE,
unlike a manager reset which sets it True. That difference is the reason this
feature is better than the path it supplements, not merely more convenient.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from identity import services
from identity.models import StaffCredential, StaffPasswordReset
from organisations.models import BusinessOrganization, OrganizationStaff

PASSWORD = "till-password-1"
NEW_PASSWORD = "a-new-one-entirely"


@override_settings(
    MAILERS={"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
)
class PasswordResetTests(TestCase):
    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Corner Shop")
        self.staff = OrganizationStaff.objects.create(
            organization=self.org,
            full_name="A Cashier",
            email="cashier@example.com",
            phone_number="0700000000",
        )
        self.credential = services.issue_credential(
            staff=self.staff, username="cashier", password=PASSWORD
        )
        mail.outbox.clear()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _request(self, username="cashier", organization=None):
        return self.client.post(
            reverse("staff-reset-request"),
            {
                "organization": self.org.pk if organization is None else organization,
                "username": username,
            },
            content_type="application/json",
        )

    def _confirm(self, code, password=NEW_PASSWORD, username="cashier"):
        return self.client.post(
            reverse("staff-reset-confirm"),
            {
                "organization": self.org.pk,
                "username": username,
                "code": code,
                "new_password": password,
            },
            content_type="application/json",
        )

    @staticmethod
    def _code_from_email():
        import re

        match = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
        assert match, "no six-digit code in the email"
        return match.group(1)

    # ── the round trip ──────────────────────────────────────────────────────

    def test_a_cashier_can_reset_and_sign_in_with_the_new_password(self):
        self.assertEqual(self._request().status_code, 202)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cashier@example.com"])

        self.assertEqual(self._confirm(self._code_from_email()).status_code, 204)

        signed_in = services.authenticate_staff(
            organization_id=self.org.pk, username="cashier", password=NEW_PASSWORD
        )
        self.assertEqual(signed_in.pk, self.credential.pk)

    def test_the_old_password_stops_working(self):
        self._request()
        self._confirm(self._code_from_email())
        with self.assertRaises(services.AuthError):
            services.authenticate_staff(
                organization_id=self.org.pk, username="cashier", password=PASSWORD
            )

    def test_the_reset_does_not_demand_another_change(self):
        """
        FALSE, unlike a manager reset. The code went to the cashier's own
        address, so the password it set is one nobody else has seen — which is
        the entire reason this path is better than asking a manager.
        """
        self.credential.must_change_password = True
        self.credential.save(update_fields=["must_change_password"])

        self._request()
        self._confirm(self._code_from_email())

        self.credential.refresh_from_db()
        self.assertFalse(self.credential.must_change_password)

    def test_a_locked_out_cashier_is_unlocked_by_resetting(self):
        """
        Otherwise you prove who you are by email and are still refused at the
        till, which is a dead end with no way out but a manager.
        """
        self.credential.locked_until = timezone.now() + timedelta(minutes=10)
        self.credential.failed_sign_ins = 5
        self.credential.save(update_fields=["locked_until", "failed_sign_ins"])

        self._request()
        self._confirm(self._code_from_email())

        self.credential.refresh_from_db()
        self.assertIsNone(self.credential.locked_until)
        self.assertEqual(self.credential.failed_sign_ins, 0)

    # ── it tells a stranger nothing ─────────────────────────────────────────

    def test_every_request_answers_the_same(self):
        """
        The property the whole endpoint rests on. Body AND status, because a
        different status is as good an oracle as a different message.
        """
        real = self._request("cashier")

        self.credential.is_active = False
        self.credential.save(update_fields=["is_active"])
        deactivated = self._request("cashier")

        unknown = self._request("nobody-by-that-name")
        wrong_org = self._request("cashier", organization=self.org.pk + 999)
        rubbish_org = self._request("cashier", organization="not-a-number")

        for other in (deactivated, unknown, wrong_org, rubbish_org):
            self.assertEqual(other.status_code, real.status_code)
            self.assertEqual(other.json(), real.json())

    def test_nothing_is_sent_for_a_username_that_does_not_exist(self):
        """The falsifiability partner: identical answers must not mean it
        mails everybody."""
        self._request("nobody-by-that-name")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_credential_in_another_shop_is_not_reachable(self):
        other_org = BusinessOrganization.objects.create(name="Other Shop")
        other_staff = OrganizationStaff.objects.create(
            organization=other_org,
            full_name="Their Cashier",
            email="theirs@example.com",
            phone_number="0700000001",
        )
        services.issue_credential(
            staff=other_staff, username="cashier", password=PASSWORD
        )
        mail.outbox.clear()

        # Same username, our organisation id. Must reach OURS, never theirs.
        self._request("cashier")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["cashier@example.com"])

    def test_no_email_on_file_sends_nothing_and_says_nothing(self):
        self.staff.email = ""
        self.staff.save(update_fields=["email"])

        response = self._request()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(mail.outbox), 0)

    # ── a code is a credential ──────────────────────────────────────────────

    def test_the_code_is_not_stored_in_the_clear(self):
        self._request()
        code = self._code_from_email()
        reset = StaffPasswordReset.objects.get()
        self.assertNotIn(code, reset.code_hashed)
        self.assertNotEqual(reset.code_hashed, code)

    def test_a_code_works_once(self):
        self._request()
        code = self._code_from_email()
        self.assertEqual(self._confirm(code).status_code, 204)
        self.assertEqual(self._confirm(code, "another-password-2").status_code, 400)

    def test_an_expired_code_is_refused(self):
        self._request()
        code = self._code_from_email()
        StaffPasswordReset.objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(self._confirm(code).status_code, 400)

    def test_guessing_is_bounded(self):
        self._request()
        real = self._code_from_email()

        for _ in range(StaffPasswordReset.MAX_ATTEMPTS):
            self.assertEqual(self._confirm("000000").status_code, 400)

        # Even the correct code is dead now.
        self.assertEqual(self._confirm(real).status_code, 400)

    def test_asking_again_kills_the_previous_code(self):
        """
        Two live codes means one that was emailed, forgotten, and left working.
        Asking for a new one says the old one is of no use.
        """
        self._request()
        first = self._code_from_email()
        self._request()
        second = self._code_from_email()

        self.assertEqual(self._confirm(first).status_code, 400)
        self.assertEqual(self._confirm(second).status_code, 204)

    def test_every_refusal_reads_the_same(self):
        self._request()
        wrong = self._confirm("000000")

        StaffPasswordReset.objects.update(used_at=timezone.now())
        spent = self._confirm("000000")
        unknown = self._confirm("000000", username="nobody")

        for other in (spent, unknown):
            self.assertEqual(other.status_code, wrong.status_code)
            self.assertEqual(other.json(), wrong.json())

    # ── it cannot flood an inbox ────────────────────────────────────────────

    def test_requests_are_capped_per_hour(self):
        for _ in range(StaffPasswordReset.MAX_PER_HOUR):
            self._request()
        self.assertEqual(len(mail.outbox), StaffPasswordReset.MAX_PER_HOUR)

        # Still 202, still silent — the cap must not become an oracle either.
        capped = self._request()
        self.assertEqual(capped.status_code, 202)
        self.assertEqual(len(mail.outbox), StaffPasswordReset.MAX_PER_HOUR)

    def test_the_cap_lifts_after_an_hour(self):
        for _ in range(StaffPasswordReset.MAX_PER_HOUR):
            self._request()
        StaffPasswordReset.objects.update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        self._request()
        self.assertEqual(len(mail.outbox), StaffPasswordReset.MAX_PER_HOUR + 1)

    # ── the password itself ─────────────────────────────────────────────────

    def test_a_weak_password_is_refused_without_burning_the_code(self):
        """
        The quality check runs BEFORE the code is spent. Rejecting afterwards
        would send somebody back to their inbox for a second code, having done
        nothing wrong except pick badly.
        """
        self._request()
        code = self._code_from_email()

        self.assertEqual(self._confirm(code, "short").status_code, 400)
        # Same code, a better password, and it still works.
        self.assertEqual(self._confirm(code, NEW_PASSWORD).status_code, 204)

    def test_other_sessions_end(self):
        """
        Somebody resetting a forgotten password may believe another person has
        been using the login. A reset that leaves their session open answers
        nothing.
        """
        session, _token = services.open_staff_session(self.credential)
        self._request()
        self._confirm(self._code_from_email())
        self.assertFalse(
            self.credential.sessions.filter(pk=session.pk, revoked_at=None).exists()
        )

    # ── the email ───────────────────────────────────────────────────────────

    def test_the_email_names_the_shop_and_carries_one_code(self):
        self._request()
        body = mail.outbox[0].body
        self.assertIn("Corner Shop", body)
        self.assertIn("15 minutes", body)
        # No link to press. Somebody who did not ask for this is told to speak
        # to their manager, not to click something in unexpected mail.
        self.assertNotIn("http", body)
