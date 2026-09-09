from rest_framework import serializers
from .models import Branches, Register, RegisterShift, staffAssignment
from organisations.models import OrganizationStaff


# relevant serializers

class BranchesSerializers(serializers.ModelSerializer):

    organization = serializers.StringRelatedField(read_only=True)
    class Meta:
        model = Branches
        fields = [
            'id',
            'branch_name',
            'branch_location',
            'branch_allocation',
            'branch_manager',
            'branch_number',
            'organization',
        ]

        read_only_fields = ['created_at', 'updated_at', 'branch_number', 'organization']

class RegisterSerializer(serializers.ModelSerializer):

    branch = serializers.PrimaryKeyRelatedField(queryset=Branches.objects.all())

    class Meta:
        model = Register
        fields = [
            'id',
            'branch',
            'name',
            'register_number',
            'is_active',
            'created_at'
        ]

        read_only_fields = ['created_at']

    def to_representation(self, instance):
        response = super().to_representation(instance)

        if instance.branch:
            response['branch'] = BranchesSerializers(instance.branch).data
        return response

class RegisterShiftSerializer(serializers.ModelSerializer):

    operator_name = serializers.CharField(
        source='operator.full_name',
        read_only=True
    )

    register_name = serializers.CharField(
        source='register.name',
        read_only=True
    )

    class Meta:
        model = RegisterShift
        fields = [
            'id',
            'register',
            'register_name',
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
            'register_name',
            'opened_at',
            'closed_at',
            'status',
        ]

    def validate_opening_cash(self, value):

        if value < 0:
            raise serializers.ValidationError(
                'Opening cash cannot be negative'
            )
        return value

    def validate_closing_cash(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError(
                'Closing cash cannot be negative'
            )
        return value

    def to_representation(self, instance):
        response =  super().to_representation(instance)

        if instance.operator:

            response['operator'] = instance.operator.full_name

        return response

class staffAssignmentsSerializer(serializers.ModelSerializer):

    staff_member = serializers.PrimaryKeyRelatedField(queryset=OrganizationStaff.objects.all())

    branch = serializers.PrimaryKeyRelatedField(queryset=Branches.objects.all())

    class Meta:
        model = staffAssignment
        fields = [
            'id',
            'staff_member',
            'branch',
            'staff_assignment',
            'is_active',
            'assigned_at'
        ]

    def to_representation(self, instance):
        response = super().to_representation(instance)

        # Check if instance is a model object (has an attribute) or a dictionary
        is_model = hasattr(instance, 'staff_member')

        # Safely fetch the human-readable staff name
        if is_model and instance.staff_member:
            response['staff_member'] = instance.staff_member.full_name
        elif isinstance(instance, dict) and 'staff_member' in response:
            # If it's a dict from a fresh POST, fetch the object from the DB to get the name
            staff_obj = OrganizationStaff.objects.filter(id=response['staff_member']).first()
            if staff_obj:
                response['staff_member'] = staff_obj.full_name

        # Safely fetch the human-readable branch name
        if is_model and instance.branch:
            response['branch'] = instance.branch.branch_name
        elif isinstance(instance, dict) and 'branch' in response:
            # If it's a dict from a fresh POST, fetch the object from the DB to get the name
            branch_obj = Branches.objects.filter(id=response['branch']).first()
            if branch_obj:
                response['branch'] = branch_obj.branch_name

        return response