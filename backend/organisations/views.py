from django.shortcuts import render
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from .models import BusinessOrganization, OrganizationStaff
from .serializers import BusinessOrganizationSerializer, OrganizationStaffSerializer
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
    """

    tenant_path = "id"
    queryset = BusinessOrganization.objects.all()
    serializer_class = BusinessOrganizationSerializer


class OrganizationsStaffViewSet(TenantScoped, viewsets.ModelViewSet):
    """Employees of the caller's own organisations, and nobody else's."""

    tenant_path = "organization_id"
    queryset = OrganizationStaff.objects.all()
    serializer_class = OrganizationStaffSerializer

