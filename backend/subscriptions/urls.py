"""Subscription routes. Mounted at /sub/ by Business_Platform/urls.py."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import EntitlementView, PlanViewSet, SubscriptionViewSet

router = DefaultRouter()
router.register(r"plans", PlanViewSet, basename="plans")
router.register(r"subscription", SubscriptionViewSet, basename="subscription")

urlpatterns = [
    path("entitlement", EntitlementView.as_view(), name="entitlement"),
    path("", include(router.urls)),
]
