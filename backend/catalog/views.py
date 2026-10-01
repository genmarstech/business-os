from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import render
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from .serializers import (
    CatalogCategoriesSerializers,
    CatalogCategoryProductSerializer,
    PriceListSerializer,
)
from .models import CatalogCategories, CatalogCategoryProduct, PriceList
from . import pricing
from identity import access
from identity.scoping import TenantScoped


# Create your views here.
@api_view(['GET'])
def greetings(request):
    message = 'Greetings from the catalog app'

    return Response({'message': message})

class CatalogCategoriesViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"
    default_permission = access.CATALOG_MANAGE
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
    }
    queryset = CatalogCategories.objects.all()
    serializer_class = CatalogCategoriesSerializers

class CatalogCategoryProductViewSets(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "organization_id"

    # A cashier reads the catalogue on every scan and may change none of it:
    # the selling price is the one field a till must not be able to edit.
    default_permission = access.CATALOG_MANAGE
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
        # Stocking a shelf is an inventory act, not a catalogue one.
        "stock": access.INVENTORY_ADJUST,
    }
    queryset = CatalogCategoryProduct.objects.select_related('category').all()
    serializer_class = CatalogCategoryProductSerializer

    def get_serializer_context(self):
        """
        Hand the serializer the resolved price for every product on the page.

        ── THE TILL MUST NOT WORK A PRICE OUT FOR ITSELF ───────────────────
        It used to read `selling_price` straight off each product, which was
        the right answer while that was the only price there was. With price
        lists it is the BASE, and a till showing the base while the checkout
        charges the list price is a customer being charged something other
        than the shelf edge said.

        So the resolved figure is computed here, by `catalog.pricing`, the
        same module `sales.services.checkout` calls — one implementation, so
        the two cannot disagree. Resolved for the whole page at once rather
        than per row: a till opens with the entire catalogue on screen, and
        per-row resolution would be two queries per product.

        `?branch=` names where. Without it only organisation-wide lists can
        apply, because guessing at a branch's own prices is how one shop's
        promotion gets shown at another.
        """
        context = super().get_serializer_context()
        if self.action not in ("list", "retrieve"):
            return context

        raw = self.request.query_params.get("branch")
        try:
            branch_id = int(raw) if raw else None
        except (TypeError, ValueError):
            branch_id = None

        page = list(self.filter_queryset(self.get_queryset()))
        context["resolved_prices"] = pricing.prices_for(page, branch_id=branch_id)
        return context

    @action(detail=True, methods=["post"])
    def stock(self, request, pk=None):
        """
        Put this product on a branch's shelf, with its opening count.

        ══════════════════════════════════════════════════════════════════════
        WITHOUT THIS, A NEW PRODUCT CANNOT BE SOLD AND NOTHING SAYS WHY.

        A product is catalogue; stock is per branch. Creating one never created
        the other, so the happy path — add a product, open the till, scan it —
        ended in "X is not stocked at this branch", with the fix sitting on a
        screen nobody had been sent to.

        The opening count is booked as a DELIVERY rather than written into the
        row, so the first number has a movement behind it like every number
        after it. See inventory/services.stock_product.
        ══════════════════════════════════════════════════════════════════════
        """
        from branches.models import Branches
        from inventory import services as inventory_services

        product = self.get_object()

        branch = Branches.objects.filter(
            pk=request.data.get("branch"), organization_id=product.organization_id
        ).first()
        if branch is None:
            # Not "forbidden": a branch outside the caller's tenant reads the
            # same as one that does not exist, as everywhere else here.
            return Response(
                {"branch": "No such branch, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            inventory = inventory_services.stock_product(
                product=product,
                branch=branch,
                quantity=request.data.get("quantity", "0"),
                note=str(request.data.get("note", "")).strip(),
            )
        except DjangoValidationError as error:
            detail = (
                error.message_dict
                if hasattr(error, "message_dict")
                else {"detail": error.messages}
            )
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "branch": branch.pk,
                "product": product.pk,
                "quantity": str(inventory.quantity),
            },
            status=status.HTTP_201_CREATED,
        )

class PriceListViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    Promotions, branch pricing and wholesale rates.

    ── NO `branch_path`, AND IT IS NOT AN OVERSIGHT ────────────────────────
    A price list reaches branches through `PriceListBranch`, and a list with
    NO branch rows applies everywhere. Scoping the queryset to the caller's
    branches would therefore hide exactly the lists that apply to them —
    `scoped_to_branch` filters on a column, and "no rows" is not a column
    value.

    It is organisation-level configuration, like `TaxRule` (§7), and gated
    the same way: anybody who can see a price can see the rule behind it,
    and changing one is catalogue management.

    Writing is held at CATALOG_MANAGE, which no operational role holds. The
    selling price is the one field a till must not be able to edit, and a
    price list is the selling price wearing a hat.
    """

    tenant_path = "organization_id"
    default_permission = access.CATALOG_MANAGE
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
    }
    queryset = (
        PriceList.objects.prefetch_related(
            "branch_links", "entries", "entries__product"
        ).all()
    )
    serializer_class = PriceListSerializer
