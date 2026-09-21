from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import greetings, BranchInventoryViewSet, StockMovementViewSet, StockTransferViewSet, StockAdjustmentViewSet, StockLevelViewSet

router = DefaultRouter()

# relevant url patterns

router.register(r"inventory", BranchInventoryViewSet, basename='inventory')
router.register(r"stock-movements", StockMovementViewSet, basename='stock-movements')
router.register(r"stock-transfers", StockTransferViewSet, basename='stock-transfers')
router.register(r"stock-adjustments", StockAdjustmentViewSet, basename='stock-adjustments')
router.register(r"stock-levels", StockLevelViewSet, basename='stock-levels')

urlpatterns = [
    path('greetings/', greetings, name='Greetings'),

    path('', include(router.urls))
]