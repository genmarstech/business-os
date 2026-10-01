"""
The Django admin login, with a second factor.

── IT REPLACES THE REAL FORM, NOT ADDS A SECOND DOOR ───────────────────────────

Installed as `admin.site.login_form` in Business_Platform/urls.py, so there is
one login form and this is it. A control on a door nobody uses is not a
control.

── PASSWORD FIRST, CODE SECOND ────────────────────────────────────────────────

`super().clean()` runs ordinary authentication, so whatever Django does about
a wrong password still happens and happens first. A second factor that let
somebody probe passwords without that would have made the first factor weaker,
not the account stronger.

── AND A WRONG CODE SAYS WHAT A WRONG PASSWORD SAYS ───────────────────────────

By calling Django's own `get_invalid_login_error()`, not by writing the
sentence out. gen-portal's first version wrote it out and the two drifted
immediately, so a wrong code announced itself as a wrong CODE — which tells an
attacker the password was right and the only thing left to get is the phone.
There is a test here comparing the two strings for exactly that reason.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django import forms


class AdminTotpLoginForm(AdminAuthenticationForm):
    token = forms.CharField(
        label="Authenticator code",
        required=False,
        max_length=16,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "placeholder": "6 digits",
            }
        ),
        help_text="From your authenticator app.",
    )

    def clean(self):
        cleaned = super().clean()

        user = self.get_user()
        if user is None:
            return cleaned

        device = getattr(user, "admin_totp", None)
        if device is None or not device.is_confirmed:
            # ── THE ROLLOUT SWITCH ──────────────────────────────────────
            # Off by default: nobody is enrolled on the day this deploys,
            # enrolling needs a shell, and demanding one first locks out the
            # person who would run it. Deploy, enrol, then set it.
            if getattr(settings, "ADMIN_REQUIRE_TOTP", False):
                raise ValidationError(
                    "This account has no authenticator set up, and one is "
                    "required. Run `manage.py enrol_admin_totp` on the server.",
                    code="totp_required",
                )
            return cleaned

        if not device.check_code(self.cleaned_data.get("token", "")):
            raise self.get_invalid_login_error()

        return cleaned
