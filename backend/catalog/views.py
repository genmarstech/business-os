from django.shortcuts import render
from rest_framework.decorators import api_view
from rest_framework import viewsets
from rest_framework.response import Response
from .serializers import CatalogCategoriesSerializers, CatalogCategoryProductSerializer
from .models import CatalogCategories, CatalogCategoryProduct
from identity.scoping import TenantScoped


# Create your views here.
@api_view(['GET'])
def greetings(request):
    message = 'Greetings from the catalog app'

    return Response({'message': message})

class CatalogCategoriesViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    queryset = CatalogCategories.objects.all()
    serializer_class = CatalogCategoriesSerializers

class CatalogCategoryProductViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    queryset = CatalogCategoryProduct.objects.select_related('category').all()
    serializer_class = CatalogCategoryProductSerializer