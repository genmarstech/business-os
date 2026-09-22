"""
What a manager sees of a till login, and what they may set.

══════════════════════════════════════════════════════════════════════════════
THE HASH IS NOT A FIELD HERE AND MUST NEVER BECOME ONE.

`password` is write-only in every direction. A serialiser that lists it — even
"just for debugging", even behind a permission — puts an Argon2 hash into a
JSON response, a browser's memory, a proxy log and whatever the client caches.
`fields` is written out explicitly rather than `exclude`, so adding a column to
StaffCredential cannot publish it by accident.
══════════════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

from rest_framework import serializers

from organisations.models import OrganizationStaff

from .models import StaffCredential, TenantInvitation, TenantMembership


class StaffCredentialSerializer(serializers.ModelSerializer):
    """
    Reading a credential, and creating one.

    ── `organization` IS ABSENT ON PURPOSE ────────────────────────────────
    Blueprint §8. It is derived from the staff record in services.issue_
    credential, which is the only way a credential is ever made. Accepting it
    here would let a caller attach a login to somebody else's shop, and
    because staff sign-in takes the organisation as a parameter, that login
    would then work there.
    """

    staff = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )
    staff_name = serializers.CharField(source="staff.full_name", read_only=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    # Derived, and more useful to a screen than `locked_until` — "locked"
    # is the question a manager is asking when somebody cannot get in.
    is_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = StaffCredential
        fields = [
            "id",
            "staff",
            "staff_name",
            "username",
            "password",
            "must_change_password",
            "is_active",
            "is_locked",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "staff_name",
            # Set by the services, never by a request: `must_change_password`
            # is an answer to "has the person themselves chosen this password",
            # which a manager cannot truthfully change by sending a field.
            "must_change_password",
            "is_locked",
            "created_at",
            "updated_at",
        ]

    def update(self, instance, validated_data):
        """
        Only `is_active` is editable, and only through the service.

        Moving `staff` would hand one person's sign-in history to another, and
        renaming a username mid-employment breaks every audit trail that
        recorded it. Both are refused rather than silently ignored, because a
        caller who sent them believes they worked.
        """
        for locked in ("staff", "username", "password"):
            if locked in validated_data:
                raise serializers.ValidationError(
                    {
                        locked: (
                            "Cannot be changed after the sign-in is created. "
                            "Withdraw it and issue a new one, or reset the "
                            "password."
                        )
                    }
                )
        return super().update(instance, validated_data)


class PasswordSerializer(serializers.Serializer):
    """A manager setting somebody else's password."""

    password = serializers.CharField(trim_whitespace=False)


class ChangeOwnPasswordSerializer(serializers.Serializer):
    """A cashier replacing their own. The current one is required — see services."""

    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)


class TenantInvitationSerializer(serializers.ModelSerializer):
    """
    An offer of authority, as a screen sees it.

    `organization` is absent on write for the same reason it is absent on a
    credential: blueprint §8. It is taken from the caller's own tenant in the
    view, never from the request — a caller who could name it could invite
    themselves into somebody else's business.
    """

    state = serializers.CharField(read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    invited_by_email = serializers.CharField(
        source="invited_by.email", read_only=True, default=""
    )

    class Meta:
        model = TenantInvitation
        fields = [
            "id",
            "email",
            "role",
            "role_label",
            "state",
            "invited_by_email",
            "created_at",
            "expires_at",
            "accepted_at",
            "revoked_at",
        ]
        read_only_fields = [
            "id",
            "role_label",
            "state",
            "invited_by_email",
            "created_at",
            # Every one of these is the service's to write. An expiry a client
            # could set is an invitation that never expires.
            "expires_at",
            "accepted_at",
            "revoked_at",
        ]


class TenantMembershipSerializer(serializers.ModelSerializer):
    """
    Who is already in the business, and with what authority.

    ⚠ NOTHING HERE IS WRITABLE. A role is changed through the viewset's
      `set-role` action and a person is removed through `remove`, both of
      which refuse to leave an organisation without an owner. A PATCH that
      could set `role` directly would route around that check.
    """

    email = serializers.CharField(source="account.email", read_only=True)
    full_name = serializers.CharField(source="account.full_name", read_only=True)
    role_label = serializers.CharField(source="get_role_display", read_only=True)
    invited_by_email = serializers.CharField(
        source="invited_by.email", read_only=True, default=""
    )

    class Meta:
        model = TenantMembership
        fields = [
            "id",
            "email",
            "full_name",
            "role",
            "role_label",
            "invited_by_email",
            "created_at",
        ]
        read_only_fields = fields
