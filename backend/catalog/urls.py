from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import greetings, CatalogCategoriesViewSets, CatalogCategoryProductViewSets

# relevant url patterns from the start

router = DefaultRouter()

router.register(r"categories", CatalogCategoriesViewSets, basename='categories')
router.register(r"products", CatalogCategoryProductViewSets, basename='products')


urlpatterns = [
    path('greetings/', greetings, name='greet'),

    path('', include(router.urls))
]