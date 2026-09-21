from django.db import transaction
from django.shortcuts import render
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from .models import BusinessOrganization, OrganizationStaff
from .serializers import BusinessOrganizationSerializer, OrganizationStaffSerializer
from rest_framework.exceptions import PermissionDenied, ValidationError

from identity import services
from identity.models import PlatformAccount
from identity import access
from identity.scoping import TenantScoped


# Create your views here.

@api_view(['GET'])
def greetings(request):
    message = 'Greetings from the organisations backend application.'

    return Response({"message": message})


class BusinessOrganizationViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    The tenants the caller belongs to. Never every tenant on the platform.

    `tenant_path` is "id" here because this IS the organisation table — the
    row's own primary key is what has to be in scope.

    ── CREATING ONE IS THE WAY IN ──────────────────────────────────────────────

    A subscriber who has just signed in through Genmars for the first time has
    no membership anywhere, so every list on this platform is empty for them and
    stays that way. This is the one endpoint that changes that, which is why it
    is reachable with an empty scope when nothing else is.
    """

    tenant_path = "id"

    # ── NO `default_permission` HERE, AND THAT IS THE POINT ────────────────
    #
    # `create` is the BOOTSTRAP. A subscriber who has just signed in through
    # Genmars holds no TenantMembership, therefore holds no permission at all,
    # and creating their first business is the act that gives them one.
    # Gating it on anything would mean nobody could ever start — the account
    # would need a membership to make the organisation that grants the
    # membership.
    #
    # Reading is left to tenant scoping for a related reason: an accountant
    # must be able to see the business they account for, and they hold no
    # settings permission.
    #
    # What IS gated is changing an organisation that already exists, which is
    # a settings act — there is no permission that lets an operational
    # principal rename the business that employs them.
    permissions = {
        "update": access.SETTINGS_MANAGE,
        "partial_update": access.SETTINGS_MANAGE,
        "destroy": access.SETTINGS_MANAGE,
    }
    queryset = BusinessOrganization.objects.all()
    serializer_class = BusinessOrganizationSerializer

    def perform_create(self, serializer):
        """
        Create the business AND make the creator its owner, or do neither.

        A till must not be able to do this. A cashier signed in at one shop
        creating a second business, which they would then own, is not a feature
        anybody asked for — operational staff exist inside a tenant and have no
        standing to make another.
        """
        account = self.request.user
        if not isinstance(account, PlatformAccount):
            raise PermissionDenied(
                "Only somebody signed in with a Genmars account can create a "
                "business."
            )

        with transaction.atomic():
            serializer.save()
            try:
                services.create_tenant(
                    account=account, organization=serializer.instance
                )
            except services.AuthError as error:
                # Rolls the organisation back with it. An organisation whose
                # creator cannot reach it is worse than a refusal, because
                # nothing on any screen will ever show it again.
                raise ValidationError({"detail": error.safe_message}) from None


class OrganizationsStaffViewSet(TenantScoped, viewsets.ModelViewSet):
    """Employees of the caller's own organisations, and nobody else's."""

    tenant_path = "organization_id"
    # OrganizationStaff carries a concrete `branch` FK, so the list is a
    # branch's list. Only an owner holds STAFF_MANAGE today and owners are
    # organisation-wide, so this changes nothing now — it is here for the day
    # a branch manager is granted it, which is when forgetting would show a
    # manager every other branch's employees.
    branch_path = "branch_id"
    default_permission = access.STAFF_MANAGE
    queryset = OrganizationStaff.objects.all()
    serializer_class = OrganizationStaffSerializer

