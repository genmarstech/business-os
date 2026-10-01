from django.contrib import admin

from .models import (
    GoodsReceipt,
    GoodsReceiptItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)


class PurchaseOrderItemInline(admin.TabularInline):
    model = PurchaseOrderItem
    extra = 0


class GoodsReceiptItemInline(admin.TabularInline):
    model = GoodsReceiptItem
    extra = 0


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "phone_number", "is_active")
    list_filter = ("organization", "is_active")
    search_fields = ("name", "phone_number", "email")


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ("number", "organization", "branch", "supplier", "status", "total")
    list_filter = ("organization", "status")
    inlines = [PurchaseOrderItemInline]


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("number", "organization", "branch", "purchase_order", "received_at")
    list_filter = ("organization",)
    inlines = [GoodsReceiptItemInline]
