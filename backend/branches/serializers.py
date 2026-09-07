from rest_framework import serializers
from .models import Branches

# relevant serializers

class BranchesSerializers(serializers.ModelSerializer):

    class Meta:
        model = Branches
        fields = [
            'id',
            'branch_name',
            'branch_location',
            'branch_allocation',
            'branch_manager',
            'branch_number',
        ]

        read_only_fields = ['created_at', 'updated_at', 'branch_number']