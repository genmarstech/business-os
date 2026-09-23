"""
The Resend backend.

The test that matters most is test_a_body_is_never_logged. Everything this
backend will ever carry is somebody's credential or somebody's business, and a
log line is the one place a secret ends up without anybody deciding to put it
there. The rest of these are ordinary.

Nothing here reaches the network: urlopen is replaced. A test suite that talks
to Resend would burn quota, fail offline, and send real email from CI.
"""

from __future__ import annotations

import json
import logging
import urllib.error
from unittest import mock

from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from Business_Platform.mail_backends import ResendBackend

BACKEND = "Business_Platform.mail_backends.ResendBackend"
KEY = "re_test_not_a_real_key"


class _Response:
    """Enough of an HTTP response for the backend's `with` block."""

    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok(payload=None):
    return mock.patch(
        "Business_Platform.mail_backends.urllib.request.urlopen",
        return_value=_Response(payload or {"id": "msg_123"}),
    )


@override_settings(RESEND_API_KEY=KEY, DEFAULT_FROM_EMAIL="info@genmars.co.ke")
class ResendBackendTests(SimpleTestCase):
    def _send(self, message, **backend_kwargs):
        return ResendBackend(**backend_kwargs).send_messages([message])

    def _message(self, **kwargs):
        defaults = {
            "subject": "Your password",
            "body": "Your reset code is 493021.",
            "to": ["cashier@example.com"],
        }
        defaults.update(kwargs)
        return EmailMessage(**defaults)

    # ── the payload ─────────────────────────────────────────────────────────

    def test_a_message_becomes_one_post_to_resend(self):
        with _ok() as urlopen:
            self.assertEqual(self._send(self._message()), 1)

        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        payload = json.loads(request.data)
        self.assertEqual(payload["to"], ["cashier@example.com"])
        self.assertEqual(payload["from"], "info@genmars.co.ke")

    def test_the_user_agent_is_set(self):
        """
        Load-bearing, not cosmetic: Cloudflare blocks urllib's default outright
        and answers 403 with a page that says nothing about mail. Without this
        header nothing sends and nothing explains why.
        """
        with _ok() as urlopen:
            self._send(self._message())

        headers = urlopen.call_args[0][0].headers
        agent = headers.get("User-agent", "")
        self.assertIn("business-os", agent)
        self.assertNotIn("Python-urllib", agent)

    def test_an_html_part_is_not_dropped(self):
        """
        The failure this catches is silent: the backend once sent only `text`,
        so anything built on EmailMultiAlternatives looked like it worked and
        arrived without its HTML.
        """
        message = EmailMultiAlternatives(
            subject="Your password",
            body="plain text",
            to=["cashier@example.com"],
        )
        message.attach_alternative("<p>rich</p>", "text/html")

        with _ok() as urlopen:
            self._send(message)

        payload = json.loads(urlopen.call_args[0][0].data)
        self.assertEqual(payload["html"], "<p>rich</p>")
        # Still sent, and still readable alone — a plain-text client, a screen
        # reader and a spam filter all see this one.
        self.assertEqual(payload["text"], "plain text")

    def test_a_message_with_no_recipients_is_not_sent(self):
        with _ok() as urlopen:
            self.assertEqual(self._send(self._message(to=[])), 0)
        urlopen.assert_not_called()

    # ── the key ─────────────────────────────────────────────────────────────

    @override_settings(RESEND_API_KEY="")
    def test_no_key_raises_rather_than_dropping_the_message(self):
        """
        Silence here means every reset this process sends is discarded while
        the caller is told it worked. Loud is the only correct behaviour.
        """
        with self.assertRaises(ValueError):
            self._send(self._message())

    @override_settings(RESEND_API_KEY="")
    def test_no_key_with_fail_silently_is_the_one_exception(self):
        self.assertEqual(self._send(self._message(), fail_silently=True), 0)

    # ── failures ────────────────────────────────────────────────────────────

    def test_a_rejection_raises_unless_told_otherwise(self):
        error = urllib.error.HTTPError(
            "https://api.resend.com/emails", 422, "Unprocessable", {},
            io_stub := mock.MagicMock(),
        )
        io_stub.read.return_value = b'{"message":"domain is not verified"}'
        with mock.patch(
            "Business_Platform.mail_backends.urllib.request.urlopen",
            side_effect=error,
        ):
            with self.assertRaises(urllib.error.HTTPError):
                self._send(self._message())

    def test_an_unreachable_resend_raises(self):
        with mock.patch(
            "Business_Platform.mail_backends.urllib.request.urlopen",
            side_effect=urllib.error.URLError("no route to host"),
        ):
            with self.assertRaises(urllib.error.URLError):
                self._send(self._message())

    # ── the one that matters ────────────────────────────────────────────────

    def test_a_body_is_never_logged(self):
        """
        ═══════════════════════════════════════════════════════════════════════
        THE BODY CARRIES A LIVE CREDENTIAL AND MUST NOT REACH A LOG.

        Checked on the success path AND on both failure paths, because the
        temptation to dump the message "just for debugging" arrives precisely
        when something is failing. A log file is read by more people, kept
        longer, and shipped further than anybody thinks about when adding one
        line to an except clause.
        ═══════════════════════════════════════════════════════════════════════
        """
        secret = "493021"
        message = self._message(body=f"Your reset code is {secret}.")

        cases = [
            ("success", _ok()),
            (
                "rejected",
                mock.patch(
                    "Business_Platform.mail_backends.urllib.request.urlopen",
                    side_effect=urllib.error.HTTPError(
                        "u", 422, "no", {}, mock.MagicMock(read=lambda: b"{}")
                    ),
                ),
            ),
            (
                "unreachable",
                mock.patch(
                    "Business_Platform.mail_backends.urllib.request.urlopen",
                    side_effect=urllib.error.URLError("down"),
                ),
            ),
        ]

        for label, patcher in cases:
            with self.subTest(path=label):
                with self.assertLogs("Business_Platform.mail_backends") as logs:
                    with patcher:
                        try:
                            self._send(message, fail_silently=True)
                        except Exception:  # pragma: no cover - defensive
                            pass
                written = "\n".join(logs.output)
                self.assertNotIn(secret, written, f"{label}: the code was logged")
                self.assertNotIn("Your reset code", written, label)

    def test_the_resend_id_is_logged_because_it_is_not_content(self):
        """
        The falsifiability partner for the test above. Logging nothing at all
        would pass it and leave no way to trace one missing message.
        """
        with self.assertLogs("Business_Platform.mail_backends", logging.INFO) as logs:
            with _ok({"id": "msg_abc123"}):
                self._send(self._message())
        self.assertIn("msg_abc123", "\n".join(logs.output))


class MailerSelectionTests(SimpleTestCase):
    """The key is the switch — there is no second variable to forget."""

    def test_a_key_selects_resend(self):
        from importlib import reload
        import os

        import Business_Platform.settings as conf

        with mock.patch.dict(os.environ, {"RESEND_API_KEY": KEY, "DEBUG": "True"}):
            reloaded = reload(conf)
            self.assertEqual(reloaded.MAILERS["default"]["BACKEND"], BACKEND)

    def test_no_key_leaves_it_on_the_console(self):
        from importlib import reload
        import os

        import Business_Platform.settings as conf

        env = {k: v for k, v in os.environ.items() if k != "RESEND_API_KEY"}
        env["DEBUG"] = "True"
        with mock.patch.dict(os.environ, env, clear=True):
            reloaded = reload(conf)
            self.assertIn("console", reloaded.MAILERS["default"]["BACKEND"])
