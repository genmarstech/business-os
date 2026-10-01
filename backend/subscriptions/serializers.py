"""Shapes for the subscription endpoints."""

from __future__ import annotations

from rest_framework import serializers

from .models import Plan, Subscription, SubscriptionEvent


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "code",
            "name",
            "description",
            "monthly_price",
            "branch_limit",
            "staff_limit",
            "register_limit",
            "sort_order",
        ]
        read_only_fields = fields


class SubscriptionEventSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionEvent
        fields = ["id", "kind", "kind_label", "actor_name", "detail", "note", "at"]
        read_only_fields = fields

    def get_actor_name(self, event) -> str:
        """
        Null actor reads as "Genmars", not as blank.

        A trial opened during onboarding genuinely had no person behind it,
        and an empty cell in a history invites the reader to assume the
        record is incomplete rather than that nobody did it.
        """
        if event.actor_account is None:
            return "Genmars"
        return event.actor_account.full_name or event.actor_account.email


class SubscriptionSerializer(serializers.ModelSerializer):
    # Derived, not stored — see the banner on the model. Serialised as a
    # plain field so a client never has to recompute it, and so there is one
    # definition of "past due" in the product.
    state = serializers.SerializerMethodField()
    state_label = serializers.SerializerMethodField()
    covered_until = serializers.DateField(read_only=True)
    grace_until = serializers.DateField(read_only=True)
    days_left = serializers.IntegerField(read_only=True)
    plan_detail = PlanSerializer(source="plan", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "state",
            "state_label",
            "plan",
            "plan_detail",
            "started_on",
            "trial_ends_on",
            "paid_until",
            "covered_until",
            "grace_until",
            "grace_days",
            "days_left",
            "cancelled_on",
            "cancellation_reason",
            "note",
        ]
        # ── EVERY FIELD IS READ-ONLY, AND THAT IS THE DESIGN ────────────────
        #
        # A PATCH that could set `paid_until` would be a payment with no
        # money behind it. Each change goes through `services.py`, which
        # writes the append-only event beside it; a field somebody can set
        # directly is a change with no record of who made it or why.
        read_only_fields = fields

    def get_state(self, subscription) -> str:
        return subscription.state()

    def get_state_label(self, subscription) -> str:
        return subscription.get_state_display()
