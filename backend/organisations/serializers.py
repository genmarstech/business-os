from rest_framework import serializers
from .models import BusinessOrganization

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

