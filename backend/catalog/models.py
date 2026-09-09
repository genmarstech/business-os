from django.db import models
from organisations.models import BusinessOrganization


# Create your models here.
class CatalogCategories(models.Model):
    organization = models.ForeignKey(BusinessOrganization, on_delete=models.CASCADE, related_name='categories')

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.organization} at {self.name}"



class CatalogCategoryProduct(models.Model):
    organization = models.ForeignKey(BusinessOrganization, on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(CatalogCategories, on_delete=models.CASCADE, related_name='products')

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    sku = models.CharField(max_length=100, unique=True)

    cost_price = models.DecimalField(max_digits=12, decimal_places=2)

    selling_price = models.DecimalField(max_digits=12, decimal_places=2)

    is_active = models.BooleanField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return f"{self.organization} for product {self.name}"