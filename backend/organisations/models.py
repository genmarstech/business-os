from django.db import models
from django.utils import timezone
import random
import string
# from .views import OrgNumberGenerator


def OrgNumberGenerator():
    prefix = 'GMBP'

    first_choice = ''.join(random.choices(string.digits, k=4))
    second_choice = ''.join(random.choices(string.digits, k=4))

    BN = f"{prefix}-{first_choice}-{second_choice}"

    while BusinessOrganization.objects.filter(org_number=BN).exists():

        first_choice = ''.join(random.choices(string.digits, k=4))
        second_choice = ''.join(random.choices(string.digits, k=4))
        BN = f"{prefix}-{first_choice}-{second_choice}"

    return BN

# Create your models here.
class BusinessOrganization(models.Model):

    class StaffSize(models.TextChoices):
        SMALL = 'SM', '5',
        MEDIUM = 'MD', '15',
        LARGE = 'LG', '25'


    name = models.CharField(max_length=15)
    staff_size = models.CharField(choices=StaffSize.choices, default=StaffSize.MEDIUM)
    org_number = models.CharField(max_length=18, default=OrgNumberGenerator, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=timezone.now)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Business Org'
        verbose_name_plural = 'Business Organizations'


    def __str__(self):
        return self.name

