from django.urls import path, include
from .views import greetings, BusinessOrganizationViewSet, OrganizationsStaffViewSet
from rest_framework.routers import DefaultRouter

# register default router
router = DefaultRouter()

# relevant paths
# generate relevant CRUD paths
router.register(r"organizations", BusinessOrganizationViewSet, basename='organizations')
router.register(r"staff", OrganizationsStaffViewSet, basename='Organization Staff')

urlpatterns = [
    path('greet/', greetings, name='Org Greetings' ),

    path('', include(router.urls))

]