from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework import viewsets
from rest_framework.response import Response
from .serializers import CatalogCategoriesSerializers, CatalogCategoryProductSerializer
from .models import CatalogCategories, CatalogCategoryProduct


# Create your views here.
@api_view(['GET'])
def greetings(request):
    message = 'Greetings from the catalog app'

    return Response({'message': message})

class CatalogCategoriesViewSets(viewsets.ModelViewSet):

    queryset = CatalogCategories.objects.all()
    serializer_class = CatalogCategoriesSerializers

class CatalogCategoryProductViewSets(viewsets.ModelViewSet):

    queryset = CatalogCategoryProduct.objects.all()
    serializer_class = CatalogCategoryProductSerializer