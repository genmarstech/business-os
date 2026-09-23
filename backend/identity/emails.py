"""
The mail this application sends.

Exactly one message so far: a till password reset code.

── THE BODY IS A LIVE CREDENTIAL FOR FIFTEEN MINUTES ───────────────────────
Nothing here may log it, and mail_backends.py is written so that nothing
downstream does either. The code is passed in rather than read from a model,
because the plaintext exists in only two places by design — this function and
the person's inbox — and a helper that could fetch it would be a third.

── PLAIN TEXT, NO HTML ─────────────────────────────────────────────────────
A cashier reads this on a phone, in a shop, possibly on a cheap handset with a
mail client nobody has heard of. Six digits in a sentence survive that. An HTML
template is a thing to maintain and a thing to render wrong, for a message
whose entire content is a number.
"""

from __future__ import annotations

from django.core.mail import EmailMessage


def send_password_reset(*, to: str, code: str, organisation: str, minutes: int) -> None:
    """
    Send one reset code.

    Deliberately not wrapped in a try/except. A caller that swallows a mail
    failure tells the cashier to check an inbox nothing was sent to — see the
    view, which reports the failure to the log and still answers uniformly.
    """
    EmailMessage(
        subject="Your till password reset code",
        body=(
            f"Someone asked to reset the till password for {organisation}.\n\n"
            f"Your code is {code}\n\n"
            f"Type it into the till. It works once and expires in {minutes} "
            "minutes.\n\n"
            # Said plainly, and without a link to click. The honest advice to
            # somebody who did not ask for this is to tell their manager, not
            # to press a button in an email they did not expect.
            "If you did not ask for this, you can ignore it — your password "
            "has not changed. Tell your manager if it keeps happening."
        ),
        to=[to],
    ).send(fail_silently=False)
