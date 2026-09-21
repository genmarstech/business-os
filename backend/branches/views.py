from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets
from .serializers import RegisterSerializer, RegisterShiftSerializer, BranchesSerializer, StaffAssignmentSerializer
from .models import Branches, Register, RegisterShift, staffAssignment
from identity.scoping import TenantScoped


# Create your views here.

@api_view(['GET'])
def greeting(request):
    message = 'Welcome to the branches api'

    return Response({'message': message})

class BranchesViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    queryset = Branches.objects.all()
    serializer_class = BranchesSerializer


class RegisterViewSets(TenantScoped, viewsets.ModelViewSet):
    """A register reaches its organisation through its branch."""

    tenant_path = "branch__organization_id"
    queryset = Register.objects.select_related('branch').all()
    serializer_class = RegisterSerializer

class RegisterShiftViewSets(TenantScoped, viewsets.ModelViewSet):
    """Two hops: shift -> register -> branch -> organisation."""

    tenant_path = "register__branch__organization_id"
    queryset = RegisterShift.objects.select_related('register__branch').all()
    serializer_class = RegisterShiftSerializer


class StaffAssignmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "branch__organization_id"
    queryset = staffAssignment.objects.select_related('branch').all()
    serializer_class = StaffAssignmentSerializer

    def perform_create(self, serializer):
        # The scope check comes FIRST. Without it the deactivation below runs
        # against a staff member the caller may not be allowed to touch — a
        # write into somebody else's shop, dressed as a helpful tidy-up.
        self.refuse_out_of_scope(serializer.validated_data)

        staff_member = serializer.validated_data['staff_member']

        # Somebody moving to a new role stops holding the old one. Filtered by
        # staff member, which is itself tenant-bound and has just been checked.
        staffAssignment.objects.filter(
            staff_member=staff_member,
            is_active=True
        ).update(is_active=False)

        serializer.save(is_active=True)