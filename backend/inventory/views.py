from django.shortcuts import render
from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets
from .models import BranchInventory
from .serializers import BranchInventorySerializer


# Create your views here.
@api_view(['GET'])
def greetings(request):
    message = 'Hello from the inventory application'

    return Response({"message": message})


class BranchInventoryViewsets(viewsets.ModelViewSet):
    queryset = BranchInventory.objects.all()
    
    serializer_class = BranchInventorySerializer
