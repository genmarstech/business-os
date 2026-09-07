from django.db import models
from django.utils import timezone
import random
import string
# from .views import OrgNumberGenerator

# this is the organization model
# if further advancement will be done to it 
# kindly consider the current model
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

# the next thing is creating the members of the organization
# and their role in the organization so as not to mix up works

def staffNumberGenerator():
    prefix = 'OSSN'

    ran_staff_no = ''.join(random.choices(string.digits, k=6))

    staff_no = f"{prefix}-{ran_staff_no}"

    # prevent duplicate staff numbers

    while OrganizationStaff.objects.filter(staff_number=staff_no).exists():
        ran_staff_no = ''.join(random.choices(string.digits, k=6))

        staff_no = f"{prefix}-{ran_staff_no}"

    return staff_no


class OrganizationStaff(models.Model):

   

    # personal details
    full_name = models.CharField(max_length=50, unique=True)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, unique=True)
    address = models.CharField('Address', max_length=255)
    city = models.CharField('City', max_length=100, blank=True, null=True)
    kra_pin = models.CharField(max_length=11, blank=True, null=True, unique=True)
    id_number = models.IntegerField(unique=True)

    # professional details
    branch = models.CharField(max_length=20, blank=True, null=True)
    start_date = models.DateField(null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=timezone.now)
    staff_number = models.CharField(max_length=20, default=staffNumberGenerator, unique=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Staff Organization'
        verbose_name_plural = 'Staff Organization'


    def __str__(self):
        return self.full_name

