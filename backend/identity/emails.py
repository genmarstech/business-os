"""
The mail this application sends.

Two messages: a till password reset code, and an invitation that carries
one.

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


def send_staff_invitation(
    *, to: str, username: str, code: str, organisation: str, minutes: int
) -> None:
    """
    Tell somebody they have a till login, and let them choose the password.

    ── IT DOES NOT CONTAIN A PASSWORD, AND THAT IS THE DESIGN ──────────────
    The obvious invitation emails a temporary password. That puts a working
    credential in an inbox for as long as the inbox exists, and inboxes are
    forwarded, shared on a shop's one laptop, and left open. It also means
    the manager who typed it knows it, so nothing the cashier does under
    that login is solely theirs until they change it — and "change it later"
    is a button people press.

    So this carries a code, the same fifteen-minute single-use code the
    forgotten-password flow uses, and the cashier picks a password nobody
    else has ever seen. The codebase already reached this conclusion for the
    reset; an invitation is the same problem on the first day.

    The code is passed in rather than read from a model, for the reason in
    the module docstring: the plaintext should exist in two places only.
    """
    EmailMessage(
        subject=f"Your till login for {organisation}",
        body=(
            f"You have been set up to work the till at {organisation}.\n\n"
            f"Your username is {username}\n"
            f"Your setup code is {code}\n\n"
            "At the till, tap \u201cForgotten your password?\u201d, type your "
            "username and this code, and choose a password. Nobody else will "
            "know it \u2014 not your manager, not Genmars.\n\n"
            f"The code works once and expires in {minutes} minutes. If it has "
            "run out, tap the same link and ask for a new one, or tell your "
            "manager.\n\n"
            # No link to click. A shop's staff are exactly the people a
            # phishing mail would target with one, and the real flow happens
            # on a till they are standing at.
            "If you were not expecting this, tell your manager."
        ),
        to=[to],
    ).send(fail_silently=False)
