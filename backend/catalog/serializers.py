from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from branches.models import Branches
from organisations.models import BusinessOrganization
from organisations.serializers import BusinessOrganizationSerializer

from .models import (
    CatalogCategories,
    CatalogCategoryProduct,
    PriceList,
    PriceListBranch,
    PriceListEntry,
)

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

        # ── `price` IS WHAT IT COSTS; `selling_price` IS THE BASE ──────────
        #
        # Equal for almost every product in almost every shop, and the whole
        # point of the field is the cases where they differ. A client draws
        # `price` and shows `selling_price` struck through beside it; nothing
        # on a screen should ever charge off `selling_price` alone.
        #
        # Falls back rather than being absent when the view did not resolve
        # anything — a missing key would make a till render "undefined" where
        # a figure belongs.
        resolved = self.context.get("resolved_prices") or {}
        price, from_list = resolved.get(
            instance.pk, (instance.selling_price, None)
        )
        response["price"] = str(price)
        response["price_list"] = from_list.pk if from_list else None
        response["price_list_name"] = from_list.name if from_list else None

        if instance.organization:

            response['organization'] = BusinessOrganizationSerializer(instance.organization).data

        if instance.category:

            response['category'] = CatalogCategoriesSerializers(instance.category).data


        return response

# ══════════════════════════════════════════════════════════════════════════════
# PRICE LISTS
# ══════════════════════════════════════════════════════════════════════════════


class PriceListEntrySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    # What it would otherwise cost, so a screen can show the override beside
    # the thing it overrides without a second request per row.
    base_price = serializers.DecimalField(
        source="product.selling_price", max_digits=12, decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = PriceListEntry
        fields = ["id", "product", "product_name", "product_sku", "base_price", "price"]


class PriceListSerializer(serializers.ModelSerializer):
    """
    A list, the branches it applies at, and the prices on it — written as one
    document.

    ── THE ENTRIES AND BRANCHES ARE REPLACED WHOLE, NOT MERGED ─────────────
    A PUT or PATCH carrying `entries` means "these are the prices now".
    Merging would make removing a product from a promotion impossible
    through the API, which is exactly the operation somebody needs on the
    morning the promotion was supposed to end.

    Leaving the key out entirely changes nothing, so renaming a list does not
    require resending every price on it.
    """

    entries = PriceListEntrySerializer(many=True, required=False)
    branches = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        queryset=Branches.objects.all(),
        help_text="Where it applies. None at all means every branch.",
    )
    in_force = serializers.SerializerMethodField()

    class Meta:
        model = PriceList
        fields = [
            "id",
            "organization",
            "name",
            "note",
            "starts_on",
            "ends_on",
            "precedence",
            "is_active",
            "in_force",
            "branches",
            "entries",
        ]

    def get_in_force(self, price_list) -> bool:
        """
        Whether it is in force TODAY — which `is_active` does not answer on
        its own, because a promotion can be active and three weeks away. A
        screen that showed only the flag would tell a shop their promotion is
        running when it is not.
        """
        return price_list.is_active and price_list.in_force_on(timezone.localdate())

    def to_representation(self, instance):
        response = super().to_representation(instance)
        response["branches"] = [
            link.branch_id for link in instance.branch_links.all()
        ]
        return response

    def validate(self, data):
        starts = data.get("starts_on", getattr(self.instance, "starts_on", None))
        ends = data.get("ends_on", getattr(self.instance, "ends_on", None))
        if starts and ends and ends < starts:
            raise serializers.ValidationError(
                {
                    "ends_on": (
                        "A list that ends before it starts is in force on no "
                        "day at all. It would look configured and do nothing."
                    )
                }
            )
        return data

    def _replace(self, price_list, *, entries=None, branches=None):
        if branches is not None:
            price_list.branch_links.all().delete()
            PriceListBranch.objects.bulk_create(
                [
                    PriceListBranch(price_list=price_list, branch=branch)
                    for branch in branches
                ]
            )
        if entries is not None:
            price_list.entries.all().delete()
            PriceListEntry.objects.bulk_create(
                [
                    PriceListEntry(
                        price_list=price_list,
                        product=entry["product"],
                        price=entry["price"],
                    )
                    for entry in entries
                ]
            )

    def create(self, validated_data):
        entries = validated_data.pop("entries", [])
        branches = validated_data.pop("branches", [])
        with transaction.atomic():
            price_list = PriceList.objects.create(**validated_data)
            self._replace(price_list, entries=entries, branches=branches)
        return price_list

    def update(self, instance, validated_data):
        # `pop` with a sentinel, not `.get(..., [])`: an absent key means
        # "leave them alone" and an empty list means "there are none now",
        # and collapsing the two makes it impossible to clear a list.
        entries = validated_data.pop("entries", None)
        branches = validated_data.pop("branches", None)
        with transaction.atomic():
            for field, value in validated_data.items():
                setattr(instance, field, value)
            instance.save()
            self._replace(instance, entries=entries, branches=branches)
        return instance
