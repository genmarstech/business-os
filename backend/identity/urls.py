"""Identity routes. Mounted at /auth/ by Business_Platform/urls.py."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

# Managing till logins is an authentication decision, so it lives with the rest
# of them rather than under /org/ with the personnel records. The two are
# genuinely different questions: OrganizationStaff answers "does this person
# work here", StaffCredential answers "may they open a till today".
router = DefaultRouter()
router.register(
    r"staff/credentials", views.StaffCredentialViewSet, basename="staff-credential"
)
# Who may administer the business, as opposed to who may work a till. Both
# live under /auth/ because both are questions about authority rather than
# about the shop's own records.
router.register(r"invitations", views.TenantInvitationViewSet, basename="invitation")
router.register(r"members", views.TenantMembershipViewSet, basename="member")

urlpatterns = [
    # Subscriber — the Genmars handoff. `callback` must match the redirect_uri
    # registered in ops exactly; the portal matches it whole, not by prefix.
    path("start", views.SignOnStartView.as_view(), name="sign-on-start"),
    path("callback", views.SignOnCallbackView.as_view(), name="sign-on-callback"),
    # Operational staff — tenant-local, never Genmars.
    path("staff/sign-in", views.StaffSignInView.as_view(), name="staff-sign-in"),
    path("staff/sign-out", views.StaffSignOutView.as_view(), name="staff-sign-out"),
    path(
        "staff/password",
        views.ChangeOwnPasswordView.as_view(),
        name="staff-change-password",
    ),
    path("me", views.WhoAmIView.as_view(), name="whoami"),
    # Last: the router's list route is `staff/credentials/`, which cannot
    # collide with the explicit paths above, but keeping it here means a future
    # `staff/<something>` path is matched by its own rule and not swallowed.
    path("", include(router.urls)),
]
