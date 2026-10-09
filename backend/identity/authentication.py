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

# ── THE OTHER SESSION KEY, AND WHY THEY MUST NEVER BOTH BE SET ──────────────
#
# A browser session can carry a subscriber OR a member of operational staff,
# and the two authentication classes below read these two keys out of the SAME
# Django session. If both were ever present, whichever class DRF happened to
# try first would decide who the caller is — and the order is a line in
# settings.py that nobody would think of as a security control.
#
# So signing in either way clears the other key first. `become()` below is the
# only thing that writes either of them, and a test asserts a session cannot
# end up holding both.
STAFF_SESSION_KEY = "staff_session_id"


def become(request, *, account_id: int | None = None, staff_session_id: int | None = None):
    """
    Make this browser session exactly one principal, and rotate its key.

    ⚠ THE ONLY PLACE EITHER SESSION KEY IS WRITTEN.

    Two reasons it is a function rather than two assignments at two call
    sites:

      · A session must never hold both keys. Which principal a request
        authenticated as would then depend on the ORDER of
        DEFAULT_AUTHENTICATION_CLASSES in settings.py — a line nobody reads
        as a security control, and one a future reshuffle would change
        without anybody connecting the two.

      · Session fixation. A session id captured before a sign-in is still
        valid after it unless the key is cycled, and the sign-in is the one
        moment in the flow where closing that costs nothing.

    Pass neither to sign out of both.
    """
    request.session.pop(SUBSCRIBER_SESSION_KEY, None)
    request.session.pop(STAFF_SESSION_KEY, None)

    if account_id is None and staff_session_id is None:
        return

    request.session.cycle_key()
    if account_id is not None:
        request.session[SUBSCRIBER_SESSION_KEY] = account_id
    else:
        request.session[STAFF_SESSION_KEY] = staff_session_id


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


class StaffSessionAuthentication(authentication.BaseAuthentication):
    """
    A member of operational staff working in a browser, not at a till.

    ══════════════════════════════════════════════════════════════════════════
    WHY THIS EXISTS WHEN StaffTokenAuthentication ALREADY DOES.

    The bearer token below was chosen for a TILL, and the argument for it is
    on StaffSession: a till is not a browser tab, it stays signed in for a
    whole shift, it is meant to become an offline-capable client, and it holds
    its credential explicitly rather than having one set on it invisibly.

    Every word of that is about a register. None of it is about a finance
    clerk reading yesterday's takings on a laptop, or a purchasing officer
    approving a delivery — and those people had NOWHERE TO WORK. The
    server-rendered application was already written for them: Shell.tsx gates
    every navigation item on a permission, and half a dozen pages branch on
    `me.kind === "staff"`. They simply could not reach it, because a server
    component forwards cookies and they had only a token.

    So the transport differs by context and the CREDENTIAL does not. Both
    classes resolve the same StaffCredential, through the same
    `authenticate_staff`, into the same StaffPrincipal.

    ⚠ THIS DOES NOT BREACH THE TWO-TIER RULE, AND IT IS WORTH SAYING WHY.
      The rule is that the two CREDENTIAL STORES must never meet: a
      StaffCredential password must never be accepted by api.genmars.co.ke,
      and a Genmars password must never be accepted here. This adds no third
      store and no crossover — it is the same tenant-local credential, read
      from a cookie this application set rather than from a header. A
      subscriber still cannot sign in this way and a cashier still cannot sign
      in at Genmars.
    ══════════════════════════════════════════════════════════════════════════

    ── IT ENFORCES CSRF, FOR THE REASON THE SUBSCRIBER CLASS DOES ──────────
    A cookie is sent by the browser automatically, so a cross-site POST would
    otherwise ride on it. The bearer class below deliberately does NOT enforce
    CSRF, and must not: a header is never sent automatically, so there is
    nothing to forge, and enforcing it would break every till.

    That difference is the whole reason these are two classes rather than one
    with a branch. A single class would have to decide per request whether the
    check applies, and the day somebody gets that branch wrong it fails open.
    """

    def authenticate(self, request):
        session_id = request.session.get(STAFF_SESSION_KEY)
        if not session_id:
            return None

        # Anything that is not an integer is not one of ours. Passing it to
        # the ORM raises ValueError and turns a bad session into a 500 — the
        # same trap SubscriberSessionAuthentication already guards, found
        # there by a test that put a staff token in the subscriber's key.
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            request.session.pop(STAFF_SESSION_KEY, None)
            return None

        session = services.resolve_staff_session_by_id(session_id)
        if session is None:
            # Expired, revoked, or the login was withdrawn. Checked on EVERY
            # request rather than only at the door, which is what makes
            # `set_credential_active(active=False)` take effect now rather
            # than whenever the browser next signs in.
            request.session.pop(STAFF_SESSION_KEY, None)
            return None

        # Only once we know there IS a session to protect — running it before
        # would answer "CSRF failed" to an anonymous caller.
        self.enforce_csrf(request)

        return (StaffPrincipal(session), None)

    def enforce_csrf(self, request):
        """Django's own check, run by hand because this is not middleware."""
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
