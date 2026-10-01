"""
A 500 reaches a human, and does not take a credential with it.

═══════════════════════════════════════════════════════════════════════════════
WHY THIS FILE EXISTS.

There was no ADMINS, no LOGGING and no handler. An un-caught exception went to
the container log and stopped there, and a container log is a thing somebody
reads *after* being told there is a problem. The way Genmars would have found
out a till was failing is a shop telephoning — the shop was the monitoring.

Two claims are worth defending and both are tested here: that an error is
actually mailed, and that the mail is safe to send. The second matters more.
An alert that carries a decrypted Daraja passkey or a cashier's password out
through a third-party relay has turned a fault into a disclosure.
═══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings


class ErrorAlertTests(TestCase):
    def raise_one(self, *, path="/anything", extra=None):
        """
        Log through `django.request` the way Django's own handler does, with a
        real exception attached, so AdminEmailHandler builds the report it
        would build in production.
        """
        request = RequestFactory().get(path)
        try:
            # A local that must never travel. Named like nothing Django's
            # own filter looks for, because the point is that the plain
            # report does not carry locals at all.
            the_passkey = "not-a-real-passkey-but-shaped-like-one"  # noqa: F841
            raise RuntimeError("something came apart")
        except RuntimeError:
            logging.getLogger("django.request").error(
                "Internal Server Error: %s",
                path,
                exc_info=True,
                extra={"status_code": 500, "request": request, **(extra or {})},
            )

    @override_settings(DEBUG=False)
    def test_an_uncaught_error_is_mailed(self):
        self.raise_one()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("something came apart", mail.outbox[0].body)

    @override_settings(DEBUG=False)
    def test_the_mail_carries_no_local_variables(self):
        """
        ══════════════════════════════════════════════════════════════════
        THE ONE THAT MATTERS.

        Django's HTML traceback embeds every local in every frame. At the
        moment of a 500 here that plausibly means a decrypted passkey, a
        cashier's password, a customer's phone number or another business's
        basket. `include_html=False` is what keeps them out, and this is
        what notices if somebody turns it back on for readability.
        ══════════════════════════════════════════════════════════════════
        """
        self.raise_one()
        sent = mail.outbox[0]

        self.assertNotIn("not-a-real-passkey", sent.body)
        # An HTML alternative is where the locals would be, so there must
        # not be one at all.
        self.assertEqual(sent.alternatives, [])

    @override_settings(DEBUG=True)
    def test_nothing_is_mailed_while_somebody_is_developing(self):
        """
        Otherwise a developer mistyping a URL emails a real person. That is
        how an alert channel becomes something nobody reads.
        """
        self.raise_one()
        self.assertEqual(len(mail.outbox), 0)

    def test_the_handler_is_actually_wired_to_django_request(self):
        """
        The configuration, not the behaviour — because the behaviour above
        would pass just as well if some *other* logger happened to be
        carrying it, and `django.request` is the one Django uses.
        """
        request_logger = settings.LOGGING["loggers"]["django.request"]
        self.assertIn("mail_admins", request_logger["handlers"])
        self.assertFalse(request_logger.get("propagate", True))

    def test_the_html_report_is_off_in_the_configuration_too(self):
        """
        Belt and braces over the behavioural test: somebody reading settings
        should see the intent, and somebody changing it should trip this.
        """
        self.assertFalse(
            settings.LOGGING["handlers"]["mail_admins"]["include_html"]
        )

    def test_there_is_somebody_to_mail(self):
        """ADMINS empty means AdminEmailHandler does nothing, silently."""
        self.assertTrue(settings.ADMINS)
        self.assertTrue(all(address for _, address in settings.ADMINS))
