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

    # --------------------------------------------------------
    # ORGANIZATION
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

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
            'is_active',
            'created_at',
            'updated_at',
        ]

        read_only_fields = [
            'id',
            'branch_number',
            'created_at',
            'updated_at',
        ]


# ============================================================
# REGISTER
# ============================================================

class RegisterSerializer(serializers.ModelSerializer):

    # --------------------------------------------------------
    # BRANCH
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

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
            'created_at',
        ]


# ============================================================
# REGISTER SHIFT
# ============================================================

class RegisterShiftSerializer(serializers.ModelSerializer):

    # --------------------------------------------------------
    # REGISTER
    # --------------------------------------------------------

    # Read
    register = RegisterSerializer(
        read_only=True
    )

    # Write
    register_id = serializers.PrimaryKeyRelatedField(
        queryset=Register.objects.all(),
        source='register',
        write_only=True
    )

    # --------------------------------------------------------
    # OPERATOR
    # --------------------------------------------------------

    operator = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
    )

    operator_name = serializers.CharField(
        source='operator.full_name',
        read_only=True
    )

    # --------------------------------------------------------
    # META
    # --------------------------------------------------------

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
    # STAFF MEMBER
    # --------------------------------------------------------

    staff_member = serializers.PrimaryKeyRelatedField(
        queryset=OrganizationStaff.objects.all()
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
    # ROLE
    # --------------------------------------------------------

    staff_assignment_display = serializers.CharField(
        source='get_staff_assignment_display',
        read_only=True
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
            'staff_assignment_display',
            'is_active',
            'assigned_at',
        ]

        read_only_fields = [
            'id',
            'staff_member_name',
            'staff_assignment_display',
            'assigned_at',
        ]