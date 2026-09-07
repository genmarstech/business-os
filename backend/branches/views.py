from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import viewsets
from .serializers import BranchesSerializers
from .models import Branches


# Create your views here.

@api_view(['GET'])
def greeting(request):
    message = 'Welcome to the branches api'

    return Response({'message': message})

class BranchesViewSets(viewsets.ModelViewSet):

    queryset = Branches.objects.all()
    serializer_class = BranchesSerializers
