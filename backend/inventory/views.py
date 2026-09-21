from rest_framework.response import Response
from rest_framework.decorators import api_view
from rest_framework import viewsets

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

class BranchInventoryViewSet(viewsets.ModelViewSet):
    serializer_class = BranchInventorySerializer

    def get_queryset(self):
        user = self.request.user

        return BranchInventory.objects.filter(
            branch__organization__staff__external_user_id=user.id
        ).select_related(
            "branch",
            "product",
        ).distinct()


# ---------------------------------------------------------
# Stock Movement
# ---------------------------------------------------------

class StockMovementViewSet(viewsets.ModelViewSet):
    serializer_class = StockMovementSerializer

    def get_queryset(self):
        user = self.request.user

        return StockMovement.objects.filter(
            inventory__branch__organization__staff__external_user_id=user.id
        ).select_related(
            "inventory",
            "inventory__branch",
            "inventory__product",
        ).distinct()

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------

class StockTransferViewSet(viewsets.ModelViewSet):
    serializer_class = StockTransferSerializer

    def get_queryset(self):
        user = self.request.user

        return StockTransfer.objects.filter(
            from_branch__organization__staff__external_user_id=user.id
        ).select_related(
            "from_branch",
            "to_branch",
            "product",
        ).distinct()

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------
# Stock Adjustment
# ---------------------------------------------------------

class StockAdjustmentViewSet(viewsets.ModelViewSet):
    serializer_class = StockAdjustmentSerializer

    def get_queryset(self):
        user = self.request.user

        return StockAdjustment.objects.filter(
            inventory__branch__organization__staff__external_user_id=user.id
        ).select_related(
            "inventory",
            "inventory__branch",
            "inventory__product",
        ).distinct()

    def perform_create(self, serializer):
        serializer.save()


# ---------------------------------------------------------
# Stock Level
# ---------------------------------------------------------

class StockLevelViewSet(viewsets.ModelViewSet):
    serializer_class = StockLevelSerializer

    def get_queryset(self):
        user = self.request.user

        return StockLevel.objects.filter(
            inventory__branch__organization__staff__external_user_id=user.id
        ).select_related(
            "inventory",
            "inventory__branch",
            "inventory__product",
        ).distinct()

    def perform_create(self, serializer):
        serializer.save()

