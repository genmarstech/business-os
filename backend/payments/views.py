"""
The M-Pesa endpoints.

── ONE OF THESE IS PUBLIC, AND IT IS THE ONLY ONE ──────────────────────────────

`MpesaCallbackView` has no authentication, because Safaricom cannot hold a
session. Everything about it is built on the assumption that whoever is calling
it is not Safaricom:

  · the URL carries a 32-byte token that exists only in the request we sent
  · the token is matched against a stored HASH, so a database read does not
    hand anybody a working callback URL
  · the body is **not parsed and not believed** — nothing a stranger posts
    decides whether a shop has been paid
  · all a valid token does is prompt us to ask Safaricom through the query
    API, which is the only thing that settles a push

A forged callback therefore costs us one outbound query and achieves nothing.

Worth adding later, and deliberately not relied on: an IP allowlist of
Safaricom's published ranges. It is defence in depth rather than the defence —
a design that needs the allowlist to be correct is a design that breaks when
Safaricom add a range.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from identity import access
from identity.authentication import StaffPrincipal
from identity.permissions import tenant_scope
from identity.scoping import TenantScoped

from . import crypto, services
from .models import MpesaTill, StkPush
from .serializers import (
    MpesaTillSerializer,
    RequestPaymentSerializer,
    StkPushSerializer,
)


def _refuse(error: DjangoValidationError) -> Response:
    detail = (
        error.message_dict
        if hasattr(error, "message_dict")
        else {"detail": error.messages}
    )
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


def _acting(request):
    user = request.user
    return user.staff if isinstance(user, StaffPrincipal) else user


def _callback_base(request) -> str:
    """
    The address Safaricom will post back to.

    Taken from the request rather than configured, so a deployment behind a
    different hostname does not need a second place to keep it in step.
    Safaricom require https and a public name; a sandbox run from a laptop
    needs a tunnel, which is a documented fact of Daraja rather than
    something this code can fix.
    """
    return f"{request.scheme}://{request.get_host()}"


class MpesaTillViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    The shop's own M-Pesa configuration.

    Held at SETTINGS_ORGANISATION — entering a merchant credential is the
    owner's act, not an administrator's and certainly not a cashier's. A till
    needs M-Pesa to WORK; it has no business reading how it is wired.
    """

    tenant_path = "organization_id"
    default_permission = access.SETTINGS_ORGANISATION
    permissions = {
        "list": access.SETTINGS_ORGANISATION,
        "retrieve": access.SETTINGS_ORGANISATION,
    }
    queryset = MpesaTill.objects.all()
    serializer_class = MpesaTillSerializer
    pagination_class = None

    def create(self, request, *args, **kwargs):
        if not crypto.is_configured():
            return self._unconfigured()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not crypto.is_configured():
            return self._unconfigured()
        return super().update(request, *args, **kwargs)

    def _unconfigured(self) -> Response:
        """
        The platform cannot hold secrets. Say so plainly rather than storing
        them in the clear — see the banner in crypto.py.
        """
        return Response(
            {
                "detail": (
                    "This installation cannot store M-Pesa credentials yet: "
                    "MPESA_CREDENTIAL_KEY is not configured. Nothing has been "
                    "saved."
                )
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class StkPushViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Asking a customer to pay, and watching for the answer.

    Read-only as a model viewset — a push is written by `services.request`
    and settled by Safaricom. The two custom actions are the whole write
    surface.
    """

    tenant_path = "organization_id"
    branch_path = "branch_id"
    default_permission = access.SALES_CHECKOUT
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
        # Taking a payment is checkout. Whoever may ring up a sale may ask
        # for the money for it, and nobody else.
        "request_payment": access.SALES_CHECKOUT,
        "check": access.SALES_CHECKOUT,
        "recheck": access.SALES_CHECKOUT,
    }
    queryset = StkPush.objects.select_related("branch").all()
    serializer_class = StkPushSerializer

    @action(detail=False, methods=["post"], url_path="request")
    def request_payment(self, request):
        form = RequestPaymentSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        branch = form.validated_data["branch"]

        # ── TENANT FIRST, THEN BRANCH. THEY ARE DIFFERENT QUESTIONS. ───────
        #
        # `access.may(user, perm, branch_id)` answers "may they do this at
        # that branch", and for a SUBSCRIBER the answer is branch-agnostic:
        # `branch_scope` returns None for organisation-wide authority, so
        # `may` would say yes about a branch belonging to another business
        # entirely. It is not lying — it was never asked which organisation.
        #
        # So the tenant check comes first, and it is reported as invalid
        # input rather than as forbidden, because "not yours" and "does not
        # exist" must read identically (identity/scoping.py).
        if branch.organization_id not in tenant_scope(request.user):
            return Response(
                {"branch": "No such record, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # §8: a branch id in a request body is not authority. Now ask whether
        # this caller may check out THERE, not merely somewhere.
        if not access.may(request.user, access.SALES_CHECKOUT, branch.pk):
            return Response(
                {"detail": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            push = services.request(
                branch=branch,
                amount=form.validated_data["amount"],
                phone=form.validated_data["phone_number"],
                callback_base=_callback_base(request),
                actor=_acting(request),
                description=form.validated_data.get("description") or "Payment",
            )
        except DjangoValidationError as error:
            return _refuse(error)

        return Response(
            StkPushSerializer(push).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get", "post"])
    def check(self, request, pk=None):
        """
        What happened to this push.

        ── THE TILL POLLS THIS, AND IT ASKS SAFARICOM ITSELF ───────────────
        Not "read the row and hope a callback updated it". Callbacks get
        lost — a dropped connection, a misconfigured proxy, a Safaricom
        hiccup — and a till that could only learn the answer from one would
        hang on every lost callback. This asks, every time, until the push
        settles.
        """
        push = self.get_object()
        settled = services.confirm(push)
        return Response(StkPushSerializer(settled).data)

    @action(detail=True, methods=["post"])
    def recheck(self, request, pk=None):
        """
        Ask again about a push that was closed without being paid.

        ── NOT THE SAME AS `check`, AND DELIBERATELY A SEPARATE DOOR ───────
        `check` is the poll loop and stops at the first settled answer.
        This reopens one that settled unpaid and asks Safaricom once more.
        It exists because the first production run closed a push on an
        answer that meant "still going", and the customer's money arrived
        afterwards with nothing left that would look for it.

        It decides nothing itself — `services.recheck` clears the
        settlement and lets `confirm` write whatever Safaricom says, so
        this cannot manufacture a payment. Held at SALES_CHECKOUT, the same
        as `check`: whoever may take the money may ask what became of it.
        """
        push = self.get_object()
        return Response(StkPushSerializer(services.recheck(push)).data)


class MpesaCallbackView(APIView):
    """
    Where Safaricom posts the result. Public; see the banner above.

    Always answers 200 with Daraja's expected acknowledgement shape, even for
    a token that matches nothing. A 404 here would tell whoever is probing
    which tokens are real, and Safaricom retry anything that is not a 200 —
    so a refusal would also mean being retried for ever.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request, token: str):
        push = services.note_callback(token)
        if push is not None:
            # Settle on Safaricom's own answer, not on anything in this
            # request. A failure to reach them leaves the push waiting for
            # the till's next poll.
            try:
                services.confirm(push)
            except Exception:  # noqa: BLE001
                # Never let a callback 500: Safaricom would retry it for
                # hours. The till's polling is the backstop.
                pass
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
