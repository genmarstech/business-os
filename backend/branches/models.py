from django.db import models
from django.utils import timezone
import random
import string

# Create your models here.

def branch_number_generator():
    prefix = 'BPBN'

    branch_number = ''.join(random.choices(string.digits, k=6))

    generated_number = f"{prefix}-{branch_number}"

    while Branches.objects.filter(branch_number=generated_number).exists():
        branch_number = ''.join(random.choices(string.digits, k=6))

        generated_number = f"{prefix}-{branch_number}"

    return generated_number

class Branches(models.Model):
    branch_name = models.CharField(max_length=38)
    branch_location = models.CharField(max_length=155)
    branch_allocation = models.CharField(max_length=255)
    branch_manager = models.CharField(max_length=15, unique=True)
    branch_number = models.CharField(default=branch_number_generator, max_length=18)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=timezone.now)