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

    # ── DECLARED, BECAUSE DRF WOULD OTHERWISE DEMAND IT ─────────────────────
    #
    # The model has blank=True, which normally makes a field optional. It does
    # not here: `barcode` is part of a UniqueConstraint, and DRF marks every
    # field in one as required so it can run the uniqueness check.
    #
    # Most products have no barcode at all — loose goods, services, anything
    # sold by weight — so requiring one would make the catalogue unusable for
    # half the shops this is sold to. The constraint itself already excludes
    # empties, so a blank value is never checked for uniqueness.
    barcode = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )

    class Meta:
        model = CatalogCategoryProduct
        fields = [
            'id',
            'organization',
            'category',
            'name',
            'description',
            'sku',
            # ── BOTH OF THESE WERE ON THE MODEL AND NOT HERE ───────────────
            # A field a serializer does not list is a field the API does not
            # have. `barcode` is what a scanner sends and what the till
            # resolves a scan with; `tax_rule` decides what a sale charges.
            # Adding the columns without adding them here left the checkout
            # able to read both and nothing able to set either.
            'barcode',
            'tax_rule',
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