"""Identity routes. Mounted at /auth/ by Business_Platform/urls.py."""

from django.urls import path

from . import views

urlpatterns = [
    # Subscriber — the Genmars handoff. `callback` must match the redirect_uri
    # registered in ops exactly; the portal matches it whole, not by prefix.
    path("start", views.SignOnStartView.as_view(), name="sign-on-start"),
    path("callback", views.SignOnCallbackView.as_view(), name="sign-on-callback"),
    # Operational staff — tenant-local, never Genmars.
    path("staff/sign-in", views.StaffSignInView.as_view(), name="staff-sign-in"),
    path("staff/sign-out", views.StaffSignOutView.as_view(), name="staff-sign-out"),
    path("me", views.WhoAmIView.as_view(), name="whoami"),
]
