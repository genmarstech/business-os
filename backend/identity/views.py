"""
The doors.

Four of them: two for a subscriber arriving from Genmars, two for a till.
"""

from __future__ import annotations

from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer, TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, services, signon
from .authentication import SUBSCRIBER_SESSION_KEY, StaffPrincipal
from .models import PlatformAccount
from .permissions import IsKnownPrincipal, tenant_scope


def wants_html(request) -> bool:
    """
    Is this a browser following a redirect, or code calling an API?

    Asked of the ACCEPT header rather than guessed from a user agent. A browser
    asks for text/html explicitly; curl, a server and anything scripted send
    `*/*` and must keep getting JSON.
    """
    return "text/html" in request.META.get("HTTP_ACCEPT", "")


class LandingView(View):
    """
    The root of business.genmars.co.ke.

    It exists because the alternative is what was there before: Django's bare
    404, served to anyone who types the domain — including the subscriber who
    was told to go there and sign in. There is no frontend to route to yet, so
    the front door is a page and a button rather than a redirect into
    /auth/start; a bare redirect means the domain can never be looked at
    without being pushed into somebody else's login flow.

    A plain Django view, not an APIView. Nothing here is an API.
    """

    def get(self, request):
        return render(request, "identity/landing.html")


class SignOnStartView(APIView):
    """
    Send the person to Genmars to sign in.

    A redirect rather than JSON: this is reached by a browser following a link,
    not by code. It sets the `state` in the session on the way past.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            return redirect(signon.start_url(request))
        except signon.SignOnError as error:
            return Response(
                {"detail": error.safe_message}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SignOnCallbackView(APIView):
    """
    Where Genmars sends them back, with a code.

    Order matters and is not an accident: **state first**, before the code is
    used for anything. A callback whose state does not match the session it
    arrives in is somebody else's link being clicked, and the code in it must
    never be spent.

    ── A BROWSER IS SENT ON; CODE IS ANSWERED ──────────────────────────────
    This is the only endpoint here reached by a browser rather than by code.
    It used to render a "you are signed in, there is nowhere to go" page,
    which was true while no frontend existed. There is one now, so a browser
    is redirected into it and the page is gone.

    The JSON body is unchanged and remains the contract for anything calling
    this as an API. JSONRenderer is listed FIRST on purpose: content
    negotiation walks the client's Accept header in quality order, so a
    browser — which asks for text/html at q=1 — is redirected, while curl and
    anything else sending `*/*` falls to the first renderer and still gets the
    body. Reversing the two would quietly turn every scripted call into a 302.

    ⚠ The redirect target is "/", which Caddy routes to the Next application
      and NOT back here. Sending them to a path Django also serves would loop.

    ══════════════════════════════════════════════════════════════════════════
    @ensure_csrf_cookie IS WHAT MAKES ANY SUBSCRIBER WRITE POSSIBLE AT ALL.

    This response is the ONLY one in the whole flow that Django hands to the
    browser. Every other call is made by the Next server on the subscriber's
    behalf, so every other Set-Cookie we send lands on a `fetch` response that
    is read for its body and thrown away.

    /auth/me carries the same decorator and looks like it should do this job.
    It cannot: lib/session.ts calls it server-to-server, so the token it mints
    never reaches the person who has to echo it back. That was the state of
    things, and it meant the browser held `sessionid` and nothing else —
    Django's double-submit check therefore refused EVERY subscriber POST with
    "CSRF cookie not set". Creating a business, adding a branch, changing a
    price: none of them could ever have worked.

    Taking this decorator off puts that back. There is no second mechanism.
    ══════════════════════════════════════════════════════════════════════════
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    renderer_classes = [JSONRenderer, TemplateHTMLRenderer]

    def get(self, request):
        try:
            signon.check_state(request, request.GET.get("state", ""))

            code = request.GET.get("code", "").strip()
            if not code:
                raise signon.SignOnError("no_code")

            payload = signon.exchange_code(code)
            account = services.accept_genmars_account(payload)
        except (signon.SignOnError, services.AuthError) as error:
            return Response(
                {"detail": getattr(error, "safe_message", signon.SIGN_ON_FAILED)},
                status=status.HTTP_400_BAD_REQUEST,
                template_name="identity/sign_on_failed.html",
            )

        # New session key on sign-in. Without this, a session id captured before
        # the handoff is still valid after it — session fixation, and the one
        # moment in the flow where it is cheap to close.
        request.session.cycle_key()
        request.session[SUBSCRIBER_SESSION_KEY] = account.pk

        memberships = list(services.tenants_for(account))

        # A browser goes to the application. The client decides between the
        # dashboard and the "create your business" screen from /auth/me —
        # `needs_a_business` below is the same fact, for API callers.
        if wants_html(request):
            return redirect("/")

        return Response(
            {
                "account": {"email": account.email, "full_name": account.full_name},
                # The tenants they may actually act in — from our own
                # membership table, never from the token's `organisations`.
                "organisations": [
                    {"id": m.organization_id, "name": m.organization.name,
                     "role": m.role}
                    for m in memberships
                ],
                # ── AN ONBOARDING HINT, AND NOTHING MORE ────────────────────
                #
                # The businesses this person deals with AT GENMARS. Offered so
                # a first-run screen can ask "you already deal with us as
                # Kilimani Dental — is this that business?" instead of making
                # somebody retype a name we already know.
                #
                # ⚠ IT CONFERS NOTHING. It is echoed straight from the token,
                # is never stored as authority, and no queryset anywhere is
                # filtered by it. If it is ever used to decide what somebody
                # may see, the isolation layer has been bypassed entirely.
                # Answering "yes, that is us" writes
                # BusinessOrganization.genmars_organisation_id, which records
                # who we invoice and still grants nobody anything.
                "genmars_organisations": payload.get("organisations", []),
                # True for somebody arriving for the first time. The client
                # uses it to decide between the dashboard and the "create your
                # business" screen; the server does not care either way.
                "needs_a_business": not memberships,
            }
        )


class StaffSignInView(APIView):
    """
    A till signing in, against ONE named organisation.

    The organisation is part of the credential, not something resolved from the
    username — so there is no username that works across shops, and no way to
    reach another tenant's staff by guessing one.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        organization = request.data.get("organization")
        try:
            organization_id = int(organization)
        except (TypeError, ValueError):
            return Response(
                {"detail": services.GENERIC_SIGN_IN_FAILURE},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            credential = services.authenticate_staff(
                organization_id=organization_id,
                username=request.data.get("username", ""),
                password=request.data.get("password", ""),
            )
        except services.AuthError as error:
            # One status and one message for every cause — unknown username,
            # wrong password, locked, deactivated. Anything else is a way to
            # enumerate a shop's staff from outside it.
            return Response(
                {"detail": error.safe_message}, status=status.HTTP_401_UNAUTHORIZED
            )

        session, token = services.open_staff_session(credential)

        return Response(
            {
                # Shown once. Nothing stored here can reproduce it.
                "token": token,
                "expires_at": session.expires_at,
                "must_change_password": credential.must_change_password,
                "staff": {
                    "name": credential.staff.full_name,
                    "username": credential.username,
                },
                "organisation": {
                    "id": credential.organization_id,
                    "name": credential.organization.name,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class StaffSignOutView(APIView):
    """Close the shift's session. Idempotent — signing out twice is not an error."""

    def post(self, request):
        if isinstance(request.user, StaffPrincipal):
            services.close_staff_session(request.user.session)
        request.session.pop(SUBSCRIBER_SESSION_KEY, None)
        return Response(status=status.HTTP_204_NO_CONTENT)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class WhoAmIView(APIView):
    """
    What the caller is, what they may touch, and what they may do.

    ── IT ALSO HANDS OUT THE CSRF COOKIE, AND SOMETHING HAS TO ────────────
    Django writes `csrftoken` only when a request actually asks for a token —
    a template rendering {% csrf_token %}, or this decorator. Nothing in a
    JSON API does that by itself, so without this the cookie never exists, a
    client can never read a token to echo, and EVERY write is refused with
    "CSRF cookie not set" by the check in authentication.py.

    This endpoint is where it belongs rather than a dedicated /csrf route:
    every client calls it on boot to find out who it is talking to, so the
    token arrives with the answer instead of needing its own round trip.

    Useful to a client on boot, and useful in review: if this ever reports a
    scope wider than the caller's memberships, the isolation layer is wrong and
    this is where it shows.

    ── THE PERMISSION LIST IS FOR DRAWING A SCREEN, NOT FOR GUARDING ONE ───
    A frontend needs it to decide whether to render a Refund button. That is
    the whole of what it is for. Every endpoint checks the same permissions
    again server-side, because a list returned to a browser is a list the
    browser can edit — `identity/scoping.py` and `sales/views.py` do the
    enforcing, and this only saves the user being offered something they will
    be refused.

    `branches` is null for organisation-wide authority, mirroring
    `access.branch_scope`: null means UNRESTRICTED, an empty array means
    confined to nothing. A client that treats null as "no branches" will show
    an owner an empty shop.
    """

    # NOT IsTenantMember. See the banner on IsKnownPrincipal: a subscriber
    # with no business yet must be able to read this, or they can never learn
    # they have none and can never obtain the CSRF token to create one.
    permission_classes = [IsKnownPrincipal]

    def get(self, request):
        principal = request.user

        if isinstance(principal, StaffPrincipal):
            return Response(
                {
                    "kind": "staff",
                    "name": principal.staff.full_name,
                    "username": principal.credential.username,
                    "organisation": {
                        "id": principal.organization_id,
                        "name": principal.organization.name,
                    },
                    "scope": tenant_scope(principal),
                    "branches": access.branch_scope(principal),
                    "permissions": sorted(access.granted(principal)),
                    # Per branch as well as overall, because a cashier at one
                    # branch and a manager at another holds different
                    # permissions in each — and a screen drawn from the union
                    # would offer a Refund button at the till where it will be
                    # refused.
                    "permissions_by_branch": {
                        str(branch_id): sorted(
                            access.granted(principal, branch_id)
                        )
                        for branch_id in (access.branch_scope(principal) or [])
                    },
                }
            )

        if isinstance(principal, PlatformAccount):
            return Response(
                {
                    "kind": "subscriber",
                    "email": principal.email,
                    "full_name": principal.full_name,
                    "organisations": [
                        {"id": m.organization_id, "name": m.organization.name,
                         "role": m.role}
                        for m in services.tenants_for(principal)
                    ],
                    "scope": tenant_scope(principal),
                    # None: a subscriber's authority is organisation-wide by
                    # §2, so no branch narrows it.
                    "branches": access.branch_scope(principal),
                    "permissions": sorted(access.granted(principal)),
                }
            )

        return Response({"kind": "unknown"}, status=status.HTTP_403_FORBIDDEN)
