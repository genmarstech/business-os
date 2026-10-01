from django.contrib import admin

from .models import Plan, Subscription, SubscriptionEvent


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """
    Where Genmars' plans are entered.

    The table ships empty on purpose — see the banner on the model. A price
    invented by whoever wrote the code is a price the company never agreed to,
    shown to a customer (Charter 04 §IV).
    """

    list_display = ["name", "code", "monthly_price", "branch_limit", "staff_limit",
                    "register_limit", "is_offered"]
    list_filter = ["is_offered"]
    search_fields = ["name", "code"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["organization", "plan", "trial_ends_on", "paid_until",
                    "cancelled_on"]
    list_filter = ["plan"]
    search_fields = ["organization__name"]
    raw_id_fields = ["organization"]


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    """
    Read-only, because the log is append-only. A correction is another entry.
    """

    list_display = ["subscription", "kind", "actor_account", "at"]
    list_filter = ["kind"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
