from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    greetings,
    CatalogCategoriesViewSets,
    CatalogCategoryProductViewSets,
    PriceListViewSet,
)

# relevant url patterns from the start

router = DefaultRouter()

router.register(r"categories", CatalogCategoriesViewSets, basename='categories')
router.register(r"products", CatalogCategoryProductViewSets, basename='products')
router.register(r"price-lists", PriceListViewSet, basename='price-lists')


urlpatterns = [
    path('greetings/', greetings, name='greet'),

    path('', include(router.urls))
]