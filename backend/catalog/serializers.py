from rest_framework import serializers
from .models import CatalogCategories, CatalogCategoryProduct
from organisations.models import BusinessOrganization
from organisations.serializers import BusinessOrganizationSerializer

# relevant categories
class CatalogCategoriesSerializers(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(queryset=BusinessOrganization.objects.all())

    class Meta:
        model = CatalogCategories
        fields = [
            'id',
            'organization',
            'name',
            'description',
            'is_active'
        ]

        read_only_fields = ['created_at']


    def to_representation(self, instance):
        response = super().to_representation(instance)

        if instance.organization:
            response['organization'] = BusinessOrganizationSerializer(instance.organization).data
        return response

class CatalogCategoryProductSerializer(serializers.ModelSerializer):

    organization = serializers.PrimaryKeyRelatedField(queryset=BusinessOrganization.objects.all())
    category = serializers.PrimaryKeyRelatedField(queryset=CatalogCategories.objects.all())

    class Meta:
        model = CatalogCategoryProduct
        fields = [
            'id',
            'organization',
            'category',
            'name',
            'description',
            'sku',
            'cost_price',
            'selling_price',
            'is_active',
        ]

        read_only_fields = ['created_at', 'updated_at']

    def to_representation(self, instance):

        response = super().to_representation(instance)

        if instance.organization:

            response['organization'] = BusinessOrganizationSerializer(instance.organization).data

        if instance.category:

            response['category'] = CatalogCategoriesSerializers(instance.category).data


        return response