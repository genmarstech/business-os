from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import greeting, BranchesViewSets

# relevant url patterns
router = DefaultRouter()

router.register(r"branch", BranchesViewSets, basename='Branches')

urlpatterns = [
    path('greetings/', greeting, name='Greeting'),

    path('', include(router.urls))
]