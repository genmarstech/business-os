"""
The mail this application sends.

Three messages: a till password reset code, a first-day invitation that
carries one, and an offer of authority in a business, which deliberately
carries nothing — see the banner on `send_subscriber_invitation`.

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

from django.conf import settings
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
    *,
    to: str,
    username: str,
    code: str,
    organisation: str,
    organisation_id: int,
    hours: int,
    first_time: bool,
) -> None:
    """
    Somebody's first day: where to go, who they are, and how to get a password.

    ── IT DOES NOT CONTAIN A PASSWORD, AND THAT IS THE DESIGN ──────────────
    The obvious invitation emails a temporary password. That puts a working
    credential in an inbox for as long as the inbox exists, and inboxes are
    forwarded, shared on a shop's one laptop, and left open. It also means
    the manager who typed it knows it, so nothing the cashier does under
    that login is solely theirs until they change it — and "change it later"
    is a button people press.

    So this carries a single-use code, and the cashier picks a password
    nobody else has ever seen. The codebase already reached this conclusion
    for the forgotten-password flow; a first day is the same problem earlier.

    ── IT HAS TO CARRY THE BUSINESS NUMBER, AND IT DID NOT ─────────────────
    Staff sign-in takes the organisation as a parameter — there is no
    endpoint that lists every shop on the platform and there must not be, so
    the till asks for a number and remembers it. A first-day email without
    that number describes a screen the person cannot get past. It is in here
    because this is the only message they are sent before they have one.

    ⚠ THE NUMBER IS NOT A SECRET AND IS NOT A CREDENTIAL. It is in the
      sign-in form, on the terminal they will stand at, and it grants
      nothing without a username and a password. Withholding it would only
      be withholding it from the employee.

    `first_time` changes the words and nothing else. "You asked to reset
    your password" on somebody's first morning is a message they cannot act
    on, and the two codes genuinely differ in how long they last — which is
    a sentence this has to get right, so the window is passed in from the
    row rather than worked out here.

    The code is passed in rather than read from a model, for the reason in
    the module docstring: the plaintext should exist in two places only.
    """
    where = settings.TILL_SIGN_IN_URL

    if first_time:
        opening = (
            f"You have been set up to work at {organisation}. This is how you "
            "sign in on your first day."
        )
        what_to_do = (
            f"1. Open {where} on the till, or on your phone.\n"
            f"2. Tap \u201cFirst time here?\u201d\n"
            f"3. Type your username and the setup code above.\n"
            "4. Choose your password.\n\n"
            "You go straight to your work after that \u2014 no second "
            "sign-in."
        )
        label = "Your setup code"
    else:
        opening = (
            f"Your till login at {organisation} needs a new password. Here is "
            "a code to set one."
        )
        what_to_do = (
            f"1. Open {where}.\n"
            f"2. Tap \u201cForgotten your password?\u201d\n"
            "3. Type your username and the code above.\n"
            "4. Choose your password."
        )
        label = "Your code"

    window = (
        f"The code works once and runs out in {hours} hours."
        if hours >= 2
        else "The code works once and runs out in 15 minutes."
    )

    EmailMessage(
        subject=f"Your till login for {organisation}",
        body=(
            f"{opening}\n\n"
            f"Business number: {organisation_id}\n"
            f"Username: {username}\n"
            f"{label}: {code}\n\n"
            f"{what_to_do}\n\n"
            f"{window} Nobody else will know the password you choose \u2014 "
            "not your manager, not Genmars. If the code has run out, ask for "
            "a new one on the same screen, or tell your manager.\n\n"
            # No link to click. A shop's staff are exactly the people a
            # phishing mail would target with one, and the address is typed
            # into a till they are standing at rather than followed from an
            # inbox.
            "If you were not expecting this, tell your manager."
        ),
        to=[to],
    ).send(fail_silently=False)


def send_subscriber_invitation(
    *,
    to: str,
    organisation: str,
    role: str,
    invited_by: str,
    days: int,
) -> None:
    """
    Somebody has been offered authority in a business. Tell them.

    ══════════════════════════════════════════════════════════════════════════
    THIS WAS MISSING ENTIRELY, AND NOTHING LOOKED BROKEN.

    `invite_subscriber` wrote the row and sent nothing. The owner saw the
    invitation appear in Settings → People and reasonably concluded the
    person had been told; the person was never told anything. It only ever
    worked when the invited party happened to sign on to this application by
    themselves with the matching address, which `claim_invitations` then
    attaches — so the feature appeared to work for whoever was sitting next
    to the owner and silently failed for everybody else. Three real
    invitations sat open on production having notified nobody.

    ⚠ IT CARRIES NO CODE, NO TOKEN AND NO LINK THAT GRANTS ANYTHING.

    This is not the staff invitation above and must not borrow its shape. A
    subscriber is authenticated by their GENMARS account, not by this
    application — there is no credential here to hand out, and an invitation
    that carried one would be inventing a second way into a Genmars account.
    The offer is bound to the email address and claimed only when somebody
    signs on with that address VERIFIED at Genmars. So the mail is an
    announcement: it says where to go and what will happen when they get
    there, and the authority arrives from the sign-on, not from the mail.

    That is also why there is a bare address rather than a click-through
    with a token in it. A link whose possession grants ownership of a
    business is exactly the thing worth phishing, and this one would not
    need to be: the flow works without it.
    ══════════════════════════════════════════════════════════════════════════

    Plain text, for the reason in the module docstring.
    """
    where = settings.SUBSCRIBER_SIGN_IN_URL

    # Said in terms of what the person will be able to do. "You have been
    # invited as ADMIN" is a database value read aloud.
    from .models import TenantMembership

    means = {
        TenantMembership.Role.OWNER: (
            "You will be able to run the business: its shops, its staff, its "
            "prices and who else gets in."
        ),
        TenantMembership.Role.ADMIN: (
            "You will be able to run the day-to-day: shops, staff, products "
            "and prices."
        ),
        TenantMembership.Role.ACCOUNTANT: (
            "You will be able to see the figures — sales, takings and "
            "reports — and not change how the shop is set up."
        ),
    }.get(role, "")

    who = f"{invited_by} has invited you" if invited_by else "You have been invited"

    EmailMessage(
        subject=f"You have been invited to {organisation}",
        body=(
            f"{who} to join {organisation} on the Genmars Business "
            "Platform.\n\n"
            f"{means}\n\n"
            "To accept:\n\n"
            f"1. Open {where}\n"
            "2. Sign in with your Genmars account, using this email address "
            f"({to}).\n"
            "3. That is all — you will be in the business the moment you "
            "sign in.\n\n"
            "If you have no Genmars account yet, create one with this same "
            "address and the invitation will be waiting.\n\n"
            f"The invitation runs out in {days} days. Nobody can accept it "
            "but the holder of this email address, and there is no code in "
            "this message to lose.\n\n"
            # No link to click, for the reason in the banner above.
            "If you were not expecting this, you can ignore it. Nothing has "
            "been shared with you and no account has been created."
        ),
        to=[to],
    ).send(fail_silently=False)
