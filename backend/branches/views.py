from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets
from .serializers import BranchesSerializers, RegisterSerializer, RegisterShiftSerializer, staffAssignmentsSerializer
from .models import Branches, Register, RegisterShift, staffAssignment


# Create your views here.

@api_view(['GET'])
def greeting(request):
    message = 'Welcome to the branches api'

    return Response({'message': message})

class BranchesViewSets(viewsets.ModelViewSet):

    queryset = Branches.objects.all()
    serializer_class = BranchesSerializers


class RegisterViewSets(viewsets.ModelViewSet):

    queryset = Register.objects.select_related('branch').all()
    serializer_class = RegisterSerializer

class RegisterShiftViewSets(viewsets.ModelViewSet):

    queryset = RegisterShift.objects.all()
    serializer_class = RegisterShiftSerializer


class StaffAssignmentViewSet(viewsets.ModelViewSet):

    queryset = staffAssignment.objects.all()
    serializer_class = staffAssignmentsSerializer

    def perform_create(self, serializer):
        # 1. Get the staff member from the validated data payload
        staff_member = serializer.validated_data['staff_member']
        
        # 2. Automatically find and deactivate their current active role
        staffAssignment.objects.filter(
            staff_member=staff_member, 
            is_active=True
        ).update(is_active=False)
        
        # 3. Save the new assignment (which defaults to is_active=True)
        serializer.save(is_active=True)