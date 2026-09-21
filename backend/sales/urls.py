"""Sales routes. Mounted at /sls/ by Business_Platform/urls.py."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CustomerViewSet, RefundViewSet, SaleViewSet, TaxRuleViewSet

router = DefaultRouter()
router.register(r"sales", SaleViewSet, basename="sales")
router.register(r"refunds", RefundViewSet, basename="refunds")
router.register(r"customers", CustomerViewSet, basename="customers")
router.register(r"tax-rules", TaxRuleViewSet, basename="tax-rules")

urlpatterns = [
    path("", include(router.urls)),
]
