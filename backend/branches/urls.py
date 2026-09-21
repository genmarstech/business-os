from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import greeting, BranchesViewSets, RegisterViewSets, RegisterShiftViewSets, StaffAssignmentViewSet

# relevant url patterns
router = DefaultRouter()

router.register(r"branch", BranchesViewSets, basename='Branches')
router.register(r"register", RegisterViewSets, basename='Registers')
router.register(r"register-shifts", RegisterShiftViewSets, basename='shifts')
router.register(r"staff-assignments", StaffAssignmentViewSet, basename='assignments')

urlpatterns = [
    path('greetings/', greeting, name='Greeting'),

    path('', include(router.urls))
]