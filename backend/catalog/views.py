from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import render
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response
from .serializers import CatalogCategoriesSerializers, CatalogCategoryProductSerializer
from .models import CatalogCategories, CatalogCategoryProduct
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