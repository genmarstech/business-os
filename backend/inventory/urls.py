from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import greetings, BranchInventoryViewsets

router = DefaultRouter()

# relevant url patterns

router.register(r"inventory", BranchInventoryViewsets, basename='inventory')

urlpatterns = [
    path('greetings/', greetings, name='Greetings'),

    path('', include(router.urls))
]