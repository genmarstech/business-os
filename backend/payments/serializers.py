"""Shapes for the M-Pesa endpoints."""

from __future__ import annotations

from rest_framework import serializers

from branches.models import Branches

from .models import MpesaTill, StkPush


class MpesaTillSerializer(serializers.ModelSerializer):
    """
    ── THE THREE SECRETS ARE WRITE-ONLY AND NEVER COME BACK ────────────────
    Not masked, not partially shown: absent. A field that returns four
    characters of a passkey is a field that returns four characters of a
    passkey to anybody who gets a session, and the owner who typed it does
    not need it read back.

    `*_set` booleans say whether each one is on file, which is the only
    thing a settings screen actually needs in order to say "entered" or
    "not entered".
    """

    consumer_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=True
    )
    consumer_secret = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=True
    )
    passkey = serializers.CharField(
        write_only=True, required=False, allow_blank=True, trim_whitespace=True
    )

    consumer_key_set = serializers.SerializerMethodField()
    consumer_secret_set = serializers.SerializerMethodField()
    passkey_set = serializers.SerializerMethodField()
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = MpesaTill
        fields = [
            "id",
            "organization",
            "environment",
            "short_code",
            "transaction_type",
            "account_reference",
            "is_active",
            "is_complete",
            "consumer_key",
            "consumer_secret",
            "passkey",
            "consumer_key_set",
            "consumer_secret_set",
            "passkey_set",
        ]

    def get_consumer_key_set(self, till) -> bool:
        return bool(till.consumer_key_sealed)

    def get_consumer_secret_set(self, till) -> bool:
        return bool(till.consumer_secret_sealed)

    def get_passkey_set(self, till) -> bool:
        return bool(till.passkey_sealed)

    def validate(self, data):
        """
        Turning it on is the moment the configuration has to be complete.

        Checked here rather than at save time because "on but unusable" is a
        state a shop would discover at the counter, with a customer waiting.
        """
        wants_on = data.get(
            "is_active", getattr(self.instance, "is_active", False)
        )
        if not wants_on:
            return data

        def filled(field, sealed_attr):
            if data.get(field):
                return True
            return bool(getattr(self.instance, sealed_attr, "") if self.instance else "")

        missing = [
            name
            for name, sealed in (
                ("consumer_key", "consumer_key_sealed"),
                ("consumer_secret", "consumer_secret_sealed"),
                ("passkey", "passkey_sealed"),
            )
            if not filled(name, sealed)
        ]
        if not data.get("short_code") and not getattr(self.instance, "short_code", ""):
            missing.append("short_code")

        if missing:
            raise serializers.ValidationError(
                {
                    "is_active": (
                        "Fill these in before turning M-Pesa on: "
                        + ", ".join(sorted(missing))
                    )
                }
            )
        return data

    def _apply_secrets(self, till, validated):
        """
        Blank means "leave it alone", not "clear it".

        The form cannot show what is stored, so it submits blanks for
        anything untouched. Reading a blank as "delete the passkey" would
        break the till every time somebody corrected a typo in the short
        code.
        """
        till.set_credentials(
            consumer_key=validated.get("consumer_key") or None,
            consumer_secret=validated.get("consumer_secret") or None,
            passkey=validated.get("passkey") or None,
        )

    def create(self, validated_data):
        secrets_ = {
            field: validated_data.pop(field, "")
            for field in ("consumer_key", "consumer_secret", "passkey")
        }
        till = MpesaTill(**validated_data)
        self._apply_secrets(till, secrets_)
        till.save()
        return till

    def update(self, instance, validated_data):
        secrets_ = {
            field: validated_data.pop(field, "")
            for field in ("consumer_key", "consumer_secret", "passkey")
        }
        for field, value in validated_data.items():
            setattr(instance, field, value)
        self._apply_secrets(instance, secrets_)
        instance.save()
        return instance


class StkPushSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = StkPush
        fields = [
            "id",
            "branch",
            "amount",
            "phone_number",
            "status",
            "status_label",
            "mpesa_receipt",
            "result_description",
            "sale",
            "created_at",
            "settled_at",
        ]
        # Everything. A push is written by `services.request` and settled by
        # Safaricom's own answer; a PATCH that could set `status: paid` would
        # be a payment with no payment.
        read_only_fields = fields


class RequestPaymentSerializer(serializers.Serializer):
    """
    ── THE BRANCH QUERYSET IS NOT THE TENANT CHECK ─────────────────────────
    It resolves an id into a row and nothing more. The view checks that the
    row belongs to the caller's organisation BEFORE it checks anything else,
    because `access.may(user, perm, branch_id)` does not: for a subscriber,
    `branch_scope` is None — organisation-wide authority — so `may` would
    answer True for another tenant's branch id. The branch half and the
    tenant half are different questions and only one of them is asked here.
    """

    branch = serializers.PrimaryKeyRelatedField(queryset=Branches.objects.all())
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    phone_number = serializers.CharField(max_length=20)
    description = serializers.CharField(
        max_length=40, required=False, allow_blank=True, default="Payment"
    )
