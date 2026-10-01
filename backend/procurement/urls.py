from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import GoodsReceiptViewSet, PurchaseOrderViewSet, SupplierViewSet

router = DefaultRouter()

router.register(r"suppliers", SupplierViewSet, basename="suppliers")
router.register(r"purchase-orders", PurchaseOrderViewSet, basename="purchase-orders")
router.register(r"goods-receipts", GoodsReceiptViewSet, basename="goods-receipts")

urlpatterns = [
    path("", include(router.urls)),
]
