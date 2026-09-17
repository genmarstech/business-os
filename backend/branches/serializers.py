
from rest_framework import serializers

from .models import (
    Branches,
    Register,
    RegisterShift,
    staffAssignment,
)

from organisations.models import (
    OrganizationStaff,
    BusinessOrganization,
)

from organisations.serializers import (
    BusinessOrganizationSerializer,
)


# ============================================================
# BRANCH
# ============================================================

class BranchesSerializer(serializers.ModelSerializer):

    # Read
    organization = BusinessOrganizationSerializer(
        read_only=True
    )

    # Write
    organization_id = serializers.PrimaryKeyRelatedField(
        queryset=BusinessOrganization.objects.all(),
        source='organization',
        write_only=True
    )

    class Meta:
        model = Branches

        fields = [
            'id',
            'organization',
            'organization_id',
            'branch_name',
            'branch_location',
            'branch_allocation',
            'branch_manager',
            'branch_number',
        ]

        read_only_fields = [
            'id',
            'branch_number',
        ]


# ============================================================
# REGISTER
# ============================================================

class RegisterSerializer(serializers.ModelSerializer):

    # Read
    branch = BranchesSerializer(
        read_only=True
    )

    # Write
    branch_id = serializers.PrimaryKeyRelatedField(
        queryset=Branches.objects.all(),
        source='branch',
        write_only=True
    )

    class Meta:
        model = Register

        fields = [
            'id',
            'branch',
            'branch_id',
            'name',
            'register_number',
            'is_active',
            'created_at',
        ]

        read_only_fields = [
            'id',
            'register_number',
            'created_at',
        ]


# ============================================================
# REGISTER SHIFT
# ============================================================

class RegisterShiftSerializer(serializers.ModelSerializer):

    # Read
    register = RegisterSerializer(
        read_only=True
    )

    operator_name = serializers.CharField(
        source='operator.full_name',
        read_only=True
    )

    # Write
    register_id = serializers.PrimaryKeyRelatedField(
        queryset=Register.objects.all(),
        source='register',
        write_only=True
    )

    operator = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )

    class Meta:
        model = RegisterShift

        fields = [
            'id',
            'register',
            'register_id',
            'operator',
            'operator_name',
            'opened_at',
            'closed_at',
            'opening_cash',
            'closing_cash',
            'status',
        ]

        read_only_fields = [
            'id',
            'operator_name',
            'opened_at',
            'closed_at',
            'status',
        ]

    # --------------------------------------------------------
    # OPENING CASH VALIDATION
    # --------------------------------------------------------

    def validate_opening_cash(self, value):

        if value < 0:
            raise serializers.ValidationError(
                'Opening cash cannot be negative.'
            )

        return value

    # --------------------------------------------------------
    # CLOSING CASH VALIDATION
    # --------------------------------------------------------

    def validate_closing_cash(self, value):

        if value is not None and value < 0:
            raise serializers.ValidationError(
                'Closing cash cannot be negative.'
            )

        return value


# ============================================================
# STAFF ASSIGNMENT
# ============================================================

class StaffAssignmentSerializer(serializers.ModelSerializer):

    # --------------------------------------------------------
    # STAFF
    # --------------------------------------------------------

    staff_member = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all(),
        write_only=True
    )

    staff_member_name = serializers.CharField(
        source='staff_member.full_name',
        read_only=True
    )

    # --------------------------------------------------------
    # BRANCH
    # --------------------------------------------------------

    branch = BranchesSerializer(
        read_only=True
    )

    branch_id = serializers.PrimaryKeyRelatedField(
        queryset=Branches.objects.all(),
        source='branch',
        write_only=True
    )

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

    class Meta:
        model = staffAssignment

        fields = [
            'id',
            'staff_member',
            'staff_member_name',
            'branch',
            'branch_id',
            'staff_assignment',
            'is_active',
            'assigned_at',
        ]

        read_only_fields = [
            'id',
            'staff_member_name',
            'assigned_at',
        ]
