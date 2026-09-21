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

from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets

from identity.scoping import TenantScoped

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
    queryset = BranchInventory.objects.select_related(
        'branch', 'product'
    ).all()
    serializer_class = BranchInventorySerializer



# ---------------------------------------------------------
# Stock Movement
# ---------------------------------------------------------

class StockMovementViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    queryset = StockMovement.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockMovementSerializer



# ---------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------

class StockTransferViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "from_branch__organization_id"
    queryset = StockTransfer.objects.select_related(
        'from_branch', 'to_branch', 'product'
    ).all()
    serializer_class = StockTransferSerializer



# ---------------------------------------------------------
# Stock Adjustment
# ---------------------------------------------------------

class StockAdjustmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    queryset = StockAdjustment.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockAdjustmentSerializer



# ---------------------------------------------------------
# Stock Level
# ---------------------------------------------------------

class StockLevelViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    queryset = StockLevel.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockLevelSerializer


