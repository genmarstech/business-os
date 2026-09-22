"""
The doors.

Four of them: two for a subscriber arriving from Genmars, two for a till.
"""

from __future__ import annotations

from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer, TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, services, signon
from .authentication import SUBSCRIBER_SESSION_KEY, StaffPrincipal
from .models import PlatformAccount, StaffCredential, TenantInvitation, TenantMembership
from .permissions import IsKnownPrincipal, tenant_scope
from .scoping import TenantScoped
from .serializers import (
    ChangeOwnPasswordSerializer,
    PasswordSerializer,
    StaffCredentialSerializer,
    TenantInvitationSerializer,
    TenantMembershipSerializer,
)


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

            # ── AN INVITATION BECOMES A MEMBERSHIP HERE, AND ONLY HERE ─────
            #
            # There is no link to click: an owner types an address, and the
            # next time that person completes the handoff they are admitted.
            # This is the moment their address has just been proved — the
            # token carried it and accept_genmars_account refused it unless
            # Genmars had verified it.
            #
            # Before this call existed, `create_tenant` was the only writer of
            # a TenantMembership and it always granted OWNER, so the admin and
            # accountant roles could not be given to anybody at all.
            services.claim_invitations(account)
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
                    # Its own personnel record. A till needs it to open a
                    # shift and to attribute a sale, and there is no other way
                    # for it to learn its own id: /org/staff/ is held at
                    # staff.manage, which no cashier holds. Without this the
                    # register could sign in and then do nothing.
                    #
                    # It confers nothing. Checkout pins the cashier to the
                    # authenticated principal regardless of what is sent —
                    # see sales/views.py.
                    "id": credential.staff_id,
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
                    "staff_id": principal.staff.pk,
                    "name": principal.staff.full_name,
                    "username": principal.credential.username,
                    # The till draws a "choose your own password" screen from
                    # this. Until they do, nothing they ring up is solely
                    # attributable to them — the manager typed it and knows it.
                    "must_change_password": principal.credential.must_change_password,
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


class StaffCredentialViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    Who may open a till, decided by the business that employs them.

    ══════════════════════════════════════════════════════════════════════════
    THIS IS THE ONLY WAY A TILL LOGIN COMES INTO EXISTENCE.

    Until it existed, a shop could complete onboarding, stock its shelves and
    stand somebody at a register who then had no way to sign in — the last
    step of the product was unreachable through the API at all.

    ⚠ A CREDENTIAL HERE MUST NEVER AUTHENTICATE AGAINST api.genmars.co.ke.
      Two credential stores, no crossover — CLAUDE.md, decided 2026-09-21.
      These people are the customer's employees, not Genmars'. Nothing in this
      viewset may grow a path that mints a PlatformAccount, and nothing may
      accept a Genmars token as authority over one of these rows.
    ══════════════════════════════════════════════════════════════════════════

    ── DELETE IS NOT HERE, AND THAT IS THE POINT ──────────────────────────
    `set_active` withdraws a login; the row stays. Blueprint §10 keeps
    transaction history immutable, and a sale that records who rang it up is
    worth nothing if the cashier can be deleted out from under it. Somebody
    leaving is `is_active: false`, which ends their sessions at once.
    """

    tenant_path = "organization_id"
    # StaffCredential reaches a branch only through the staff record's
    # assignments, which is a many-to-many in practice — a person can work at
    # two. There is nothing to confine here, so the organisation is the scope,
    # and STAFF_MANAGE is organisation-wide authority in every role that has
    # it. Saying so explicitly rather than leaving `branch_path` to default.
    branch_path = None
    default_permission = access.STAFF_MANAGE
    # Both match the default. Stated anyway: an action that inherits is an
    # action nobody decided about, and the day the default loosens it changes
    # silently underneath. See EveryCustomActionIsNamedTests.
    permissions = {
        "reset_password": access.STAFF_MANAGE,
        "set_active": access.STAFF_MANAGE,
    }
    queryset = StaffCredential.objects.select_related("staff", "organization")
    serializer_class = StaffCredentialSerializer
    # Deleting is refused rather than absent, so a client that tries is told
    # why instead of receiving a 405 it will read as a routing mistake.
    http_method_names = ["get", "post", "patch", "head", "options"]

    def create(self, request, *args, **kwargs):
        """
        Issue a login.

        The serializer validates shape and, through TenantScoped, that the
        staff record is the caller's own. Everything else — uniqueness, the
        password floor, the derived organisation — belongs to
        services.issue_credential, so a second caller cannot skip it.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # TenantScoped.perform_create is what normally runs the scope check on
        # the resolved foreign keys. This path does not call it, so the check
        # is run here by hand — without it, `staff` could be anybody's.
        self.refuse_out_of_scope(serializer.validated_data)

        try:
            credential = services.issue_credential(
                staff=serializer.validated_data["staff"],
                username=serializer.validated_data["username"],
                password=serializer.validated_data["password"],
            )
        except services.CredentialError as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST
            )
        except IntegrityError:
            # The uniqueness constraint, reached despite the check above by two
            # managers creating the same username at the same moment. Rare, and
            # a 500 for a race is still a bug.
            return Response(
                {"detail": "That username was just taken. Choose another."},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            self.get_serializer(credential).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request, pk=None):
        """A manager sets a new password. Every session of theirs ends with it."""
        credential = self.get_object()
        form = PasswordSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        try:
            services.reset_password(
                credential=credential, password=form.validated_data["password"]
            )
        except services.CredentialError as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(self.get_serializer(credential).data)

    @action(detail=True, methods=["post"], url_path="set-active")
    def set_active(self, request, pk=None):
        """
        Withdraw or restore the login. `{"is_active": false}`.

        Separate from PATCH so the side effect is visible in the URL: turning
        this off revokes every live session, which is not what a reader of
        `PATCH {"is_active": false}` would necessarily expect.
        """
        credential = self.get_object()
        wanted = request.data.get("is_active")
        if not isinstance(wanted, bool):
            return Response(
                {"is_active": "Send true or false."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        services.set_credential_active(credential=credential, active=wanted)
        return Response(self.get_serializer(credential).data)


class ChangeOwnPasswordView(APIView):
    """
    A cashier replacing the password their manager typed for them.

    ══════════════════════════════════════════════════════════════════════════
    THE ONLY ENDPOINT IN THE APPLICATION A TILL MAY CALL ABOUT ITS OWN
    CREDENTIAL, AND IT CAN ONLY EVER REACH ITS OWN.

    There is no id in the URL and none is accepted. The credential comes from
    the authenticated session and nothing else, so there is no parameter to
    tamper with — which is why this is a plain view rather than another action
    on the viewset above, where STAFF_MANAGE would have been required and no
    cashier holds it.
    ══════════════════════════════════════════════════════════════════════════
    """

    permission_classes = [IsKnownPrincipal]

    def post(self, request):
        if not isinstance(request.user, StaffPrincipal):
            # A subscriber has no password here to change — their identity is
            # Genmars' and is changed at Genmars.
            return Response(
                {"detail": "Only till staff have a password on this platform."},
                status=status.HTTP_403_FORBIDDEN,
            )

        form = ChangeOwnPasswordSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        try:
            services.change_own_password(
                credential=request.user.credential,
                current=form.validated_data["current_password"],
                replacement=form.validated_data["new_password"],
            )
        except services.CredentialError as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class TenantInvitationViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    Offering somebody authority in this business.

    ══════════════════════════════════════════════════════════════════════════
    THE ORGANISATION IS NEVER TAKEN FROM THE REQUEST.

    Blueprint §8. A caller who could name it could invite themselves into
    somebody else's shop as its owner — the single worst write this API could
    accept. It comes from the caller's own membership, below, and the
    serialiser does not carry the field at all.
    ══════════════════════════════════════════════════════════════════════════

    Owner only. MEMBERS_MANAGE is held by nobody else, for the reason _ADMIN
    already gives: the permission that grants every other permission is the
    narrow one.
    """

    tenant_path = "organization_id"
    branch_path = None
    default_permission = access.MEMBERS_MANAGE
    permissions = {"revoke": access.MEMBERS_MANAGE}
    queryset = TenantInvitation.objects.select_related("organization", "invited_by")
    serializer_class = TenantInvitationSerializer
    # No destroy: an invitation that was accepted is a record of how somebody
    # got in. Withdrawing one is `revoke`, which leaves the row.
    http_method_names = ["get", "post", "head", "options"]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organisation = _sole_tenant(request)
        if organisation is None:
            return Response(
                {"detail": "Only a business you own can invite anybody."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            invitation = services.invite_subscriber(
                organization=organisation,
                email=serializer.validated_data["email"],
                role=serializer.validated_data.get(
                    "role", TenantMembership.Role.ADMIN
                ),
                invited_by=request.user if isinstance(request.user, PlatformAccount) else None,
            )
        except services.InvitationError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            self.get_serializer(invitation).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        """Withdraw an offer before somebody takes it."""
        invitation = self.get_object()
        try:
            services.revoke_invitation(invitation=invitation)
        except services.InvitationError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(invitation).data)


class TenantMembershipViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Who is in this business already.

    ══════════════════════════════════════════════════════════════════════════
    AN ORGANISATION MUST NEVER BE LEFT WITHOUT AN OWNER.

    Both writes here refuse the last one. An organisation whose only owner has
    been demoted or removed is not "an organisation with no owner" — it is a
    business nobody can administer, invite into, or recover, reachable only
    from the Django admin. There is no support desk that can undo it because
    the authority to undo it is the thing that was removed.
    ══════════════════════════════════════════════════════════════════════════
    """

    tenant_path = "organization_id"
    branch_path = None
    default_permission = access.MEMBERS_MANAGE
    permissions = {
        # Anybody in the business may see who else is in it. Hiding that from
        # an accountant would mean they cannot tell who to ask about anything.
        "list": access.REPORTS_ORGANISATION,
        "retrieve": access.REPORTS_ORGANISATION,
        "set_role": access.MEMBERS_MANAGE,
        "remove": access.MEMBERS_MANAGE,
    }
    queryset = TenantMembership.objects.select_related(
        "account", "organization", "invited_by"
    )
    serializer_class = TenantMembershipSerializer

    def _would_orphan(self, membership) -> bool:
        """Is this the last owner standing?"""
        if membership.role != TenantMembership.Role.OWNER:
            return False
        others = (
            TenantMembership.objects.filter(
                organization_id=membership.organization_id,
                role=TenantMembership.Role.OWNER,
            )
            .exclude(pk=membership.pk)
            .exists()
        )
        return not others

    @action(detail=True, methods=["post"], url_path="set-role")
    def set_role(self, request, pk=None):
        membership = self.get_object()
        role = str(request.data.get("role", "")).strip()

        if role not in TenantMembership.Role.values:
            return Response(
                {"role": "That is not a role."}, status=status.HTTP_400_BAD_REQUEST
            )

        if role != TenantMembership.Role.OWNER and self._would_orphan(membership):
            return Response(
                {
                    "detail": "That is the only owner. Make somebody else an "
                              "owner first, or the business would be left with "
                              "nobody who can administer it."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.role = role
        membership.save(update_fields=["role"])
        return Response(self.get_serializer(membership).data)

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        """
        Take somebody out of the business.

        Their Genmars account is untouched — it is not ours to disable, and
        they may well administer another shop with it.
        """
        membership = self.get_object()

        if self._would_orphan(membership):
            return Response(
                {
                    "detail": "That is the only owner. A business cannot be "
                              "left with nobody who can administer it."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _sole_tenant(request):
    """
    The organisation the caller is acting in.

    A subscriber may own more than one, and this application has no tenant
    switcher yet — every other screen takes `organisations[0]` the same way.
    When a switcher lands, this is the one place that has to learn about it.
    """
    from .permissions import tenant_scope

    scope = tenant_scope(request.user)
    if len(scope) != 1:
        return None
    from organisations.models import BusinessOrganization

    return BusinessOrganization.objects.filter(pk=list(scope)[0]).first()
