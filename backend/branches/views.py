from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets
from .serializers import RegisterSerializer, RegisterShiftSerializer, BranchesSerializer, StaffAssignmentSerializer
from .models import Branches, Register, RegisterShift, staffAssignment
from identity import access
from identity.scoping import TenantScoped


# Create your views here.

@api_view(['GET'])
def greeting(request):
    message = 'Welcome to the branches api'

    return Response({'message': message})

class BranchesViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    # "id", because this IS the branch table. §5: a branch manager "should see
    # only the data and actions permitted for that branch", and a till has no
    # business enumerating the shop's other locations.
    branch_path = "id"

    # Reading the branch list is how a till knows where it is; creating and
    # renaming branches is an organisation-level act (§4).
    default_permission = access.BRANCH_MANAGE
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
    }
    queryset = Branches.objects.all()
    serializer_class = BranchesSerializer


class RegisterViewSets(TenantScoped, viewsets.ModelViewSet):
    """A register reaches its organisation through its branch."""

    tenant_path = "branch__organization_id"
    branch_path = "branch_id"
    default_permission = access.REGISTER_MANAGE
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
    }
    queryset = Register.objects.select_related('branch').all()
    serializer_class = RegisterSerializer

class RegisterShiftViewSets(TenantScoped, viewsets.ModelViewSet):
    """Two hops: shift -> register -> branch -> organisation."""

    tenant_path = "register__branch__organization_id"

    # Opening a shift is a cashier's own act; closing one is where the
    # drawer is reconciled, so it sits with the manager (module 7).
    branch_path = "register__branch_id"
    default_permission = access.SHIFT_OPEN
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
        "destroy": access.SHIFT_CLOSE,
        "update": access.SHIFT_CLOSE,
        "partial_update": access.SHIFT_CLOSE,
    }
    queryset = RegisterShift.objects.select_related('register__branch').all()
    serializer_class = RegisterShiftSerializer


class StaffAssignmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "branch__organization_id"

    # Who works where, and as what. This is the table that grants every
    # operational permission in identity/access.py, so writing to it is
    # held at the narrowest permission there is.
    branch_path = "branch_id"
    default_permission = access.STAFF_MANAGE
    permissions = {
        "list": access.STAFF_MANAGE,
        "retrieve": access.STAFF_MANAGE,
    }
    queryset = staffAssignment.objects.select_related('branch').all()
    serializer_class = StaffAssignmentSerializer

    def perform_create(self, serializer):
        # No scope check needed here: TenantScoped.create() has already run it
        # before this method is reached, which is the whole reason the guard
        # lives there rather than in perform_create.
        staff_member = serializer.validated_data['staff_member']

        # Somebody moving to a new role stops holding the old one. Filtered by
        # staff member, which is itself tenant-bound and has just been checked.
        staffAssignment.objects.filter(
            staff_member=staff_member,
            is_active=True
        ).update(is_active=False)

        serializer.save(is_active=True)