"""
The subscription endpoints.

── READ-ONLY, ON PURPOSE, AND NOT A PLACEHOLDER ────────────────────────────────

There is no endpoint that extends a subscription, and there should not be one
here. `services.extend` records that money has arrived; the thing that knows
money has arrived is gen-portal, where the invoice lives. An endpoint in this
application that moves `paid_until` is an endpoint that grants entitlement
without payment, and whoever finds it first is not a customer.

When the integration is built it belongs behind a machine credential from the
parent — the `System`/`SystemKey` shape gen-portal already has — and not
behind a subscriber's session. A subscriber must never be able to extend their
own subscription, which is precisely what reusing the session here would allow.

Cancelling is the exception and is deliberate: ending an arrangement is the
customer's own decision to make, and a product that can only be left by
emailing somebody is a product that traps people.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from identity import access
from identity.permissions import tenant_scope
from identity.scoping import TenantScoped

from . import entitlement, services
from .models import Plan, Subscription
from .serializers import (
    PlanSerializer,
    SubscriptionEventSerializer,
    SubscriptionSerializer,
)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class PlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    What Genmars offers.

    ── NOT TenantScoped, AND NOT AN OVERSIGHT ──────────────────────────────
    A plan has no organisation. It is Genmars' own price list, the same for
    everybody, and routing it through the tenant mixin would ask it to filter
    on a column that does not exist.

    `EveryViewsetIsGatedTests` walks the URLconf for `TenantScoped` viewsets
    and will not see this one; `EveryReportActionIsGatedTests` walks the rest
    and will. It answers any signed-in caller and nothing more: somebody
    deciding whether to upgrade has to see what they would be upgrading to,
    and there is nothing in a published price list worth hiding from a
    customer.

    There is NO write endpoint. Plans are Genmars' commercial terms; they are
    entered in the admin, by Genmars, and an API that let a tenant create one
    would let them create one with no limits.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = PlanSerializer
    queryset = Plan.objects.filter(is_offered=True)
    pagination_class = None


class SubscriptionViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    This tenant's own subscription.

    Held at SETTINGS_ORGANISATION — what the business pays and until when is
    the owner's and the org admin's business, not a cashier's. An accountant
    does not hold it either: this is the arrangement with Genmars rather than
    the shop's own books, and `entitlement.summary` on /auth/me already tells
    everybody what they need in order to be warned.
    """

    tenant_path = "organization_id"
    default_permission = access.SETTINGS_ORGANISATION
    permissions = {
        "list": access.SETTINGS_ORGANISATION,
        "retrieve": access.SETTINGS_ORGANISATION,
        "history": access.SETTINGS_ORGANISATION,
        "cancel": access.SETTINGS_ORGANISATION,
        "reinstate": access.SETTINGS_ORGANISATION,
    }
    serializer_class = SubscriptionSerializer
    queryset = Subscription.objects.select_related("plan", "organization")
    pagination_class = None

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        """The append-only log. Read-only because it is append-only."""
        subscription = self.get_object()
        return Response(
            SubscriptionEventSerializer(subscription.events.all(), many=True).data
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """
        End the arrangement, at the end of what has been paid for.

        Not today — see `services.cancel`. A customer who cancels on the 3rd
        having paid to the 30th keeps the month they bought.
        """
        subscription = self.get_object()
        form = CancelSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            services.cancel(
                subscription,
                reason=form.validated_data["reason"],
                actor=request.user,
            )
        except DjangoValidationError as error:
            return _refuse(error)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)

    @action(detail=True, methods=["post"])
    def reinstate(self, request, pk=None):
        subscription = self.get_object()
        try:
            services.reinstate(subscription, actor=request.user)
        except DjangoValidationError as error:
            return _refuse(error)
        subscription.refresh_from_db()
        return Response(SubscriptionSerializer(subscription).data)


def _refuse(error: DjangoValidationError) -> Response:
    detail = (
        error.message_dict
        if hasattr(error, "message_dict")
        else {"detail": error.messages}
    )
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


class EntitlementView(APIView):
    """
    What this caller's tenant may do, for drawing a banner.

    Open to any signed-in principal, unlike the subscription itself. A
    cashier is not told what the shop pays — `summary` carries no price — but
    they are told the state, because a shop with a lapsed subscription whose
    staff are the last to know is a shop where the owner finds out from a
    cashier asking why a button is missing.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = tenant_scope(request.user)
        if not scope:
            return Response({"known": False, "state": "active"})
        return Response(entitlement.summary(scope[0]))
