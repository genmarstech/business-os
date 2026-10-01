"""
M-Pesa routes.

Mounted at `/pay/` by Business_Platform/urls.py. The callback deliberately
sits at a short, stable path — it is handed to Safaricom at request time and
a URL that moves breaks every push already in flight.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import MpesaCallbackView, MpesaTillViewSet, StkPushViewSet

router = DefaultRouter()
router.register(r"mpesa/till", MpesaTillViewSet, basename="mpesa-till")
router.register(r"mpesa/pushes", StkPushViewSet, basename="mpesa-pushes")

urlpatterns = [
    path(
        "mpesa/callback/<str:token>",
        MpesaCallbackView.as_view(),
        name="mpesa-callback",
    ),
    path("", include(router.urls)),
]
