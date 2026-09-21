"""
Inventory.

── SCOPING NOTE, 2026-09-21 ────────────────────────────────────────────────────

Each viewset here used to carry its own `get_queryset` filtering on
`...organization__staff__external_user_id=user.id`. Those were replaced with
the shared `TenantScoped` mixin, for three reasons and not because there was
anything wrong with the intent:

  · `external_user_id` defaults to `uuid.uuid4()`, so it is generated HERE and
    never equals an id from anywhere else. The filter could not match a real
    caller, and the join it walked — organisation to staff to a uuid — is not
    the question being asked anyway.
  · `request.user` is now a `PlatformAccount` or a `StaffPrincipal`
    (identity/authentication.py). Neither has an `id` that is a UUID.
  · Five copies of a filter is five places for the sixth one to be forgotten.
    `identity/scoping.py` is one file, and it guards writes as well as reads —
    scoping a queryset does nothing about a create that names another shop's
    branch.

Their `select_related` choices are kept as written; they were right.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from identity import access
from identity.scoping import TenantScoped

from . import services

from .models import (
    BranchInventory,
    StockMovement,
    StockTransfer,
    StockAdjustment,
    StockLevel,
)

from .serializers import (
    BranchInventorySerializer,
    StockMovementSerializer,
    StockTransferSerializer,
    StockAdjustmentSerializer,
    StockLevelSerializer,
)


# ---------------------------------------------------------
# Greetings
# ---------------------------------------------------------

@api_view(["GET"])
def greetings(request):
    return Response({
        "message": "Hello from the inventory application"
    })


# ---------------------------------------------------------
# Branch Inventory
# ---------------------------------------------------------

class BranchInventoryViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "branch__organization_id"
    branch_path = "branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = BranchInventory.objects.select_related(
        'branch', 'product'
    ).all()
    serializer_class = BranchInventorySerializer

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """
        Change a quantity, with the reason that explains it.

        ══════════════════════════════════════════════════════════════════════
        THIS IS THE ONLY WAY A QUANTITY CHANGES OUTSIDE A SALE.

        PATCHing `quantity` on this viewset would move stock with nothing
        saying why, and stock is the one figure in a shop that cannot be
        reconstructed from anything else — a shelf count is only ever
        explained by the movements that produced it.

        ⚠ `POST /invt/stock-adjustments/` CANNOT DO THIS AND NEVER COULD.
          quantity_before and quantity_after are read-only there, correctly —
          a client that could state the "before" could state a false one — and
          nothing computed them, so the create could only fail. A shop could
          sell its stock down and had no way to book a delivery back in.
        ══════════════════════════════════════════════════════════════════════
        """
        inventory = self.get_object()

        try:
            delta = services.as_quantity(request.data.get("quantity"))
            adjustment = services.adjust(
                inventory=inventory,
                delta=delta,
                reason=str(request.data.get("reason", "")).upper(),
                note=str(request.data.get("note", "")).strip(),
            )
        except DjangoValidationError as error:
            detail = (
                error.message_dict
                if hasattr(error, "message_dict")
                else {"detail": error.messages}
            )
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        inventory.refresh_from_db()
        return Response(
            {
                "inventory": self.get_serializer(inventory).data,
                "adjustment": StockAdjustmentSerializer(adjustment).data,
            },
            status=status.HTTP_201_CREATED,
        )



# ---------------------------------------------------------
# Stock Movement
# ---------------------------------------------------------

class StockMovementViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockMovement.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockMovementSerializer



# ---------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------

class StockTransferViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "from_branch__organization_id"
    branch_path = "from_branch_id"
    default_permission = access.INVENTORY_TRANSFER
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockTransfer.objects.select_related(
        'from_branch', 'to_branch', 'product'
    ).all()
    serializer_class = StockTransferSerializer



# ---------------------------------------------------------
# Stock Adjustment
# ---------------------------------------------------------

class StockAdjustmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockAdjustment.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockAdjustmentSerializer



# ---------------------------------------------------------
# Stock Level
# ---------------------------------------------------------

class StockLevelViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockLevel.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockLevelSerializer


