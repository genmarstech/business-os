"""
The second factor on business-os's Django admin.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS GUARDS MORE THAN ANYTHING ELSE IN THE REPOSITORY.

Every other principal here is scoped: a PlatformAccount by TenantMembership, a
StaffCredential by assignment, and identity/scoping.py enforces both on every
query. A Django superuser is scoped by nothing. /admin/ reaches every shop's
takings, every staff record, every supplier and every M-Pesa configuration,
and business.genmars.co.ke answers from anywhere on the internet.

It was stock Django auth — Argon2 and password validators, but no lockout, no
rate limit, no second factor. A weaker door than gen-portal's, guarding more.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import base64
import time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from identity import totp
from identity.models import AdminTotp

PASSWORD = "correct-horse-battery-staple"
USERNAME = "founder"


def enrol(user) -> AdminTotp:
    return AdminTotp.objects.create(
        user=user, secret=totp.new_secret(), confirmed_at=timezone.now()
    )


class AdminTotpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username=USERNAME, email="founder@genmars.co.ke", password=PASSWORD
        )

    def sign_in(self, **extra):
        return self.client.post(
            reverse("admin:login"),
            {"username": USERNAME, "password": PASSWORD, "next": "/admin/", **extra},
        )

    def signed_in(self, response) -> bool:
        """A successful login redirects; a refusal re-renders the form."""
        return response.status_code == 302

    # ── the algorithm ───────────────────────────────────────────────────────

    def test_the_rfc_6238_vectors_match(self):
        """
        ── THE ONLY PROOF THAT MATTERS FOR A CRYPTO PRIMITIVE ──────────────
        Hand-rolled TOTP is defensible precisely because the answer is
        published. This is also what makes a divergence between this copy
        and gen-portal's loud rather than silent — both files are held to
        the same six numbers.
        """
        secret = base64.b32encode(b"12345678901234567890").decode()
        for unix_time, expected in [
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
            (20000000000, "353130"),
        ]:
            self.assertEqual(totp._code_at(secret, unix_time // 30), expected)

    def test_a_stale_code_is_refused(self):
        secret = totp.new_secret()
        now = time.time()
        self.assertIsNone(
            totp.verify(secret, totp._code_at(secret, totp.current_step(now) - 5),
                        at=now)
        )

    # ── the login ───────────────────────────────────────────────────────────

    def test_the_right_password_alone_is_not_enough(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE ONE THAT MATTERS. Everything else is detail.
        ══════════════════════════════════════════════════════════════════
        """
        enrol(self.user)
        self.assertFalse(self.signed_in(self.sign_in()))

    def test_the_password_and_a_code_get_in(self):
        """The control. The test above has to fail for the right reason."""
        device = enrol(self.user)
        code = totp._code_at(device.secret, totp.current_step())
        self.assertTrue(self.signed_in(self.sign_in(token=code)))

    def test_a_wrong_code_says_what_a_wrong_password_says(self):
        """
        "Password right, code wrong" tells an attacker they have a working
        password and should go and get the phone.
        """
        enrol(self.user)
        bad_code = self.sign_in(token="000000")
        bad_password = self.client.post(
            reverse("admin:login"),
            {"username": USERNAME, "password": "wrong", "next": "/admin/"},
        )
        self.assertFalse(self.signed_in(bad_code))
        self.assertFalse(self.signed_in(bad_password))

        def errors(response):
            return response.context_data["form"].errors.get("__all__", [])

        self.assertEqual(errors(bad_code), errors(bad_password))

    def test_a_code_cannot_be_used_twice(self):
        """
        Without this a code is good for its whole thirty seconds, and
        anybody who reads it over a shoulder gets in behind the person who
        typed it.
        """
        device = enrol(self.user)
        code = totp._code_at(device.secret, totp.current_step())

        self.assertTrue(self.signed_in(self.sign_in(token=code)))
        self.client.logout()
        self.assertFalse(self.signed_in(self.sign_in(token=code)))

    # ── the rollout ─────────────────────────────────────────────────────────

    def test_an_unenrolled_superuser_still_gets_in_by_default(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE DEPLOY-DAY TEST.

        Nobody has a device until somebody runs enrol_admin_totp, that needs
        a shell, and demanding one on the deploy locks out the person who
        would run it. This must stay true until ADMIN_REQUIRE_TOTP is
        deliberately set.
        ══════════════════════════════════════════════════════════════════
        """
        self.assertTrue(self.signed_in(self.sign_in()))

    @override_settings(ADMIN_REQUIRE_TOTP=True)
    def test_with_the_setting_on_no_authenticator_means_no_admin(self):
        self.assertFalse(self.signed_in(self.sign_in()))

    def test_an_unconfirmed_enrolment_enforces_nothing(self):
        """A half-finished enrolment must not lock somebody out."""
        AdminTotp.objects.create(user=self.user, secret=totp.new_secret())
        self.assertTrue(self.signed_in(self.sign_in()))

    def test_the_form_is_actually_the_one_the_admin_uses(self):
        """
        Installed by assigning admin.site.login_form BEFORE admin.site.urls
        is evaluated. Assigning it after changes a value nothing reads
        again, which is a protection that silently is not there.
        """
        from django.contrib import admin

        from identity.admin_forms import AdminTotpLoginForm

        self.assertIs(admin.site.login_form, AdminTotpLoginForm)
        self.assertIn("token", self.client.get(reverse("admin:login")).content.decode())
