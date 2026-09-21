"""
How DRF learns who is calling.

Two classes, because there are two kinds of principal and they must never be
confused for one another:

    SubscriberSessionAuthentication   a Genmars account, in a browser session
    StaffTokenAuthentication          a till, holding a bearer token

⚠ NEITHER CLASS EVER ACCEPTS THE OTHER'S CREDENTIAL.

A staff token is not a session key and a session key is not a bearer token.
There is deliberately no fallback between them, and no third class that tries
one and then the other — that is precisely how a cashier's password at one shop
turns into a way into somebody's subscriber dashboard.

── WHY NOT django.contrib.auth ─────────────────────────────────────────────────

Because using it would mean a `User` table, and a `User` has a password column.
Subscribers must not have a credential in this application — their identity is
Genmars' and is borrowed for the length of a session. So the session holds a
`PlatformAccount` id and nothing else, and `PlatformAccount` has no field that
could ever hold a secret.
"""

from __future__ import annotations

from rest_framework import authentication, exceptions

from . import services
from .models import PlatformAccount

SUBSCRIBER_SESSION_KEY = "platform_account_id"


class SubscriberSessionAuthentication(authentication.BaseAuthentication):
    """A subscriber who completed the Genmars sign-on handoff."""

    def authenticate(self, request):
        account_id = request.session.get(SUBSCRIBER_SESSION_KEY)
        if not account_id:
            return None

        # Anything that is not an integer is not one of ours. Passing it
        # straight to the ORM raises ValueError and turns a bad session into a
        # 500 — found by a test that put a staff token in this key to prove the
        # two credential stores stay apart, which is exactly the shape of thing
        # that would otherwise reach production as an availability bug.
        try:
            account_id = int(account_id)
        except (TypeError, ValueError):
            request.session.pop(SUBSCRIBER_SESSION_KEY, None)
            return None

        account = PlatformAccount.objects.filter(pk=account_id).first()
        if account is None or account.is_blocked:
            # The row vanished or was barred while the session was open.
            # Checking on every request, not only at the door, is what makes
            # blocking somebody take effect now rather than at their next login.
            request.session.pop(SUBSCRIBER_SESSION_KEY, None)
            return None

        return (account, None)


class StaffTokenAuthentication(authentication.BaseAuthentication):
    """
    A till, presenting the token it was given when the shift opened.

        Authorization: Bearer gbp_xxxxxxxx…
    """

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid authorization header.")

        session = services.resolve_staff_session(header[1].decode())
        if session is None:
            # Expired, revoked, deactivated or simply wrong — one answer for
            # all of them, so a probe learns nothing from which it got.
            raise exceptions.AuthenticationFailed("That session is no longer valid.")

        return (StaffPrincipal(session), None)

    def authenticate_header(self, request):
        return self.keyword


class StaffPrincipal:
    """
    What `request.user` is for a till.

    Not a `PlatformAccount` and not a Django `User`. Deliberately its own type,
    so a permission class or a view cannot accidentally treat a cashier as a
    subscriber by duck-typing — the two have genuinely different authority and
    the type system should say so.
    """

    is_authenticated = True

    def __init__(self, session):
        self.session = session
        self.credential = session.credential
        self.staff = session.credential.staff
        self.organization = session.credential.organization

    def __str__(self) -> str:
        return f"{self.credential.username}@{self.organization.name}"

    @property
    def organization_id(self) -> int:
        """The ONE tenant this principal may touch. Never taken from a request."""
        return self.credential.organization_id
