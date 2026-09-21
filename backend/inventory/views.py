from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from .models import BranchInventory
from identity.scoping import TenantScoped
from .serializers import BranchInventorySerializer


# Create your views here.
@api_view(['GET'])
def greetings(request):
    message = 'Hello from the inventory application'

    return Response({"message": message})


class BranchInventoryViewsets(TenantScoped, viewsets.ModelViewSet):
    """Stock reaches its organisation through the branch holding it."""

    tenant_path = "branch__organization_id"
    queryset = BranchInventory.objects.select_related('branch').all()
    
    serializer_class = BranchInventorySerializer
