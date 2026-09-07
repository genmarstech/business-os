from django.shortcuts import render
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from .models import BusinessOrganization, OrganizationStaff
from .serializers import BusinessOrganizationSerializer, OrganizationStaffSerializer
from .permissions import HasRole

# Create your views here.

@api_view(['GET'])
def greetings(request):
    message = 'Greetings from the organisations backend application.'

    return Response({"message": message})


class BusinessOrganizationViewSet(viewsets.ModelViewSet):

    queryset = BusinessOrganization.objects.all()
    serializer_class = BusinessOrganizationSerializer


class OrganizationsStaffViewSet(viewsets.ModelViewSet):
    
    queryset = OrganizationStaff.objects.all()
    serializer_class = OrganizationStaffSerializer
    permission_classes = [HasRole]

    roles_allowed = ['Org Admins']