"""
What this installation tells Genmars about itself.

═══════════════════════════════════════════════════════════════════════════════
UNTIL THIS EXISTED, business-os APPEARED NOWHERE IN THE OPS DASHBOARD.

It talked to gen-portal for sign-on and nothing else — so the one application
Genmars sells to other businesses was the one the company could not see
running. `ops.genmars.co.ke/systems` has always been there; business-os was
simply never on it.

The tests that matter here are the ones about what the report does NOT say: it
must not carry a credential, it must not carry a tenant's name, and it must
not claim to be well when it is not.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from Business_Platform import parent


@override_settings(
    GENMARS_SYSTEM_KEY="not-a-real-system-key",
    GENMARS_API_ORIGIN="https://api.genmars.co.ke",
    RESEND_API_KEY="present",
)
class HeartbeatTests(TestCase):
    def sent(self, mock):
        """The request body the module would have posted."""
        request = mock.call_args.args[0]
        return json.loads(request.data.decode())

    def test_a_healthy_installation_says_so(self):
        with patch("urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value.status = 200
            self.assertTrue(parent.heartbeat())
            self.assertEqual(self.sent(opened)["health"], "up")

    def test_no_mail_configured_is_degraded_not_up(self):
        """
        A cashier's password reset and a staff invitation both report
        success and send nothing. That is a promise silently broken, which
        is exactly what `degraded` is for.
        """
        with override_settings(RESEND_API_KEY=""):
            health, detail = parent.assess()
        self.assertEqual(health, "degraded")
        self.assertIn("mail not configured", detail)

    def test_a_switched_off_feature_is_not_degradation(self):
        """
        ── THE LINE THAT KEEPS THE SIGNAL WORTH READING ───────────────────
        M-Pesa with no credential key is a feature nobody has turned on, not
        a fault. Degrading on it would mean the dashboard showed amber from
        the day it shipped, and amber that is always on is green.
        """
        with override_settings(MPESA_CREDENTIAL_KEY=""):
            health, detail = parent.assess()
        self.assertEqual(health, "up")
        self.assertIn("tenant M-Pesa off", detail)

    def test_an_unreachable_database_is_down_and_says_nothing_about_why(self):
        """
        The exception CLASS, never its message. A connection error can carry
        the DSN and the DSN carries the password — into a field gen-portal
        renders on a dashboard.
        """
        with patch(
            "django.db.backends.utils.CursorWrapper.execute",
            side_effect=RuntimeError("could not connect: password=hunter2"),
        ):
            health, detail = parent.assess()
        self.assertEqual(health, "down")
        self.assertIn("RuntimeError", detail)
        self.assertNotIn("hunter2", detail)

    def test_the_report_carries_no_tenant_names(self):
        """
        ⚠ A SystemEvent is Genmars' operational record, not somewhere to put
        a customer's name or a figure from their till. Counts and states.
        """
        from organisations.models import BusinessOrganization

        BusinessOrganization.objects.create(name="Mama Njeri Groceries")
        _, detail = parent.assess()
        self.assertNotIn("Njeri", detail)
        self.assertIn("1 shops", detail)

    def test_the_key_travels_as_a_bearer_and_not_in_the_body(self):
        with patch("urllib.request.urlopen") as opened:
            opened.return_value.__enter__.return_value.status = 200
            parent.heartbeat()
            request = opened.call_args.args[0]

        self.assertEqual(
            request.get_header("Authorization"), "Bearer not-a-real-system-key"
        )
        self.assertNotIn("not-a-real-system-key", request.data.decode())

    def test_the_detail_cannot_outgrow_the_column_it_lands_in(self):
        """HeartbeatView truncates at 300; sending more is sending nothing."""
        _, detail = parent.assess()
        self.assertLessEqual(len(detail), 300)


class NotRegisteredTests(TestCase):
    """
    An installation with no key is an ordinary thing, not a broken one: a
    developer's laptop, or a copy somebody else runs.
    """

    @override_settings(GENMARS_SYSTEM_KEY="")
    def test_it_sends_nothing_and_does_not_raise(self):
        with patch("urllib.request.urlopen") as opened:
            self.assertFalse(parent.heartbeat())
            opened.assert_not_called()


@override_settings(GENMARS_SYSTEM_KEY="not-a-real-system-key")
class FailureTests(TestCase):
    def test_a_refusal_is_reported_without_logging_the_key(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "u", 401, "Unauthorized", {}, None
            ),
        ):
            with self.assertLogs("Business_Platform.parent", level="ERROR") as logged:
                self.assertFalse(parent.heartbeat())

        written = "\n".join(logged.output)
        self.assertIn("401", written)
        # A 401 means the key is wrong, and proving that by logging it would
        # put it in the log of the application it protects.
        self.assertNotIn("not-a-real-system-key", written)

    def test_the_command_exits_non_zero_when_it_cannot_report(self):
        """
        So the timer's OnFailure fires. A heartbeat failing silently leaves
        the dashboard showing last-seen-hours-ago, which reads as "the
        application is down" when only the reporting is.
        """
        from django.core.management import call_command

        with patch("Business_Platform.parent.heartbeat", return_value=False):
            with self.assertRaises(SystemExit):
                call_command("report_health")
