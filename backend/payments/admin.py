from django.contrib import admin

from .models import MpesaTill, StkPush


@admin.register(MpesaTill)
class MpesaTillAdmin(admin.ModelAdmin):
    """
    The sealed columns are excluded, not merely read-only.

    A read-only field still RENDERS its value, and these hold other
    companies' merchant credentials. There is no reason for a Genmars
    administrator to see the ciphertext and no way for them to use it.
    """

    list_display = ["organization", "short_code", "environment", "is_active"]
    list_filter = ["environment", "is_active"]
    search_fields = ["organization__name", "short_code"]
    raw_id_fields = ["organization"]
    exclude = ["consumer_key_sealed", "consumer_secret_sealed", "passkey_sealed"]


@admin.register(StkPush)
class StkPushAdmin(admin.ModelAdmin):
    """Read-only: a push is settled by Safaricom, never by a person here."""

    list_display = ["created_at", "organization", "amount", "phone_number",
                    "status", "mpesa_receipt"]
    list_filter = ["status"]
    search_fields = ["phone_number", "mpesa_receipt", "checkout_request_id"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
