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

from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import authentication, exceptions

from . import services
from .models import PlatformAccount

SUBSCRIBER_SESSION_KEY = "platform_account_id"


class _Csrf(CsrfViewMiddleware):
    """Django's own check, reachable from outside the middleware chain."""

    def _reject(self, request, reason):
        return reason


class SubscriberSessionAuthentication(authentication.BaseAuthentication):
    """
    A subscriber who completed the Genmars sign-on handoff.

    ══════════════════════════════════════════════════════════════════════════
    IT ENFORCES CSRF, AND SUBCLASSING BaseAuthentication IS WHY IT HAS TO.

    DRF enforces CSRF inside `SessionAuthentication` and nowhere else. A
    cookie-authenticated class that does not inherit from it therefore gets no
    check at all — which is what this was. Every subscriber write was a
    cross-site POST away from happening in somebody's logged-in browser: create
    a branch, change a price, rename the business.

    SESSION_COOKIE_SAMESITE = "Lax" was carrying the whole defence, and Lax is
    a mitigation rather than a protection. It is SITE-scoped, so it does
    nothing about another *.genmars.co.ke origin — and this company runs four
    of them. Defence in depth means both, which is also Django's own advice.

    ⚠ StaffTokenAuthentication below does NOT do this, and must not. A bearer
      token is not sent automatically by a browser, so there is nothing to
      forge; enforcing CSRF on it would only break every till.
    ══════════════════════════════════════════════════════════════════════════
    """

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

        # Only once we know there IS a session to protect. Running it before
        # would answer "CSRF failed" to an anonymous caller, which is both
        # wrong and confusing.
        self.enforce_csrf(request)

        return (account, None)

    def enforce_csrf(self, request):
        """
        Django's check, run by hand because this is not middleware.

        Safe methods pass untouched — CsrfViewMiddleware exempts GET, HEAD,
        OPTIONS and TRACE itself, so this costs a read nothing.

        The test client's `enforce_csrf_checks=False` still works: Django sets
        `_dont_enforce_csrf_checks` on the request and process_view honours it,
        so the existing suite is unaffected and a test that wants the real
        behaviour asks for it with Client(enforce_csrf_checks=True).
        """
        check = _Csrf(lambda request: None)
        check.process_request(request)
        reason = check.process_view(request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF failed: {reason}")


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
