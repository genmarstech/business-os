from rest_framework import serializers
from .models import BusinessOrganization, OrganizationStaff

# relevant serializers

class BusinessOrganizationSerializer(serializers.ModelSerializer):

    # this makes the staff size display visible and readable
    staff_size_display = serializers.CharField(source='get_staff_size_display', read_only=True)

    class Meta:
        model = BusinessOrganization
        fields = [
            'id',
            'name',
            'staff_size',
            'staff_size_display',
            'org_number',
            'updated_at'
        ]


        read_only_fields = ['created_at', 'updated_at', 'org_number']


# organization members and staff serializers
# they can be edited to the systems liking but consider the current one
class OrganizationStaffSerializer(serializers.ModelSerializer):

    class Meta:
        model = OrganizationStaff
        fields = [
            'id',
            'full_name',
            'email',
            'phone_number',
            'address',
            'city',
            'kra_pin',
            'id_number',
            'branch',
            'staff_number'
        ]


        read_only_fields = ['created_at', 'staff_number', 'updated_at']
