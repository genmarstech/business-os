import uuid

from django.db import models
from django.utils import timezone
import random
import string
# from branches.models import Branches
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


    # NOT globally unique, and not 15 characters.
    #
    # Two unrelated customers may both be called Naivas, and refusing the
    # second is both wrong and a leak: the error would tell whoever is
    # onboarding that the name is taken by a business they cannot see.
    # `org_number` is the unique identifier, and it is generated.
    name = models.CharField(max_length=150)
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

    # External identity from the company/authentication backend
    external_user_id = models.UUIDField(
        unique=True,
        db_index=True,
        editable=False,
        default=uuid.uuid4,
    )

    # ── personal details ────────────────────────────────────────────────
    #
    # ⚠ NONE OF THESE ARE GLOBALLY UNIQUE, AND THAT IS THE POINT.
    #
    # They were. Every one of them. Which meant two shops could not both
    # employ a John Mwangi, one person could not work at two businesses, and —
    # worse — a uniqueness error told whoever was adding a cashier that the
    # email or ID already existed IN SOMEBODY ELSE'S SHOP. A uniqueness
    # constraint is an oracle, and tenant isolation has to survive it.
    #
    # Scoped per organisation in Meta below, where uniqueness is meaningful at
    # all. full_name is not unique even then: two people at one shop may share
    # a name, and the platform is not the place to argue with that.
    full_name = models.CharField(
        max_length=50,
    )
    email = models.EmailField()
    phone_number = models.CharField(
        max_length=20,
    )
    address = models.CharField(
        'Address',
        max_length=255,
    )
    city = models.CharField(
        'City',
        max_length=100,
        blank=True,
        null=True,
    )

    # ⚠ NATIONAL ID AND TAX PIN ARE OTHER PEOPLE'S PERSONAL DATA.
    #
    # These belong to the CUSTOMER'S employees, which makes Genmars a processor
    # under the Kenyan Data Protection Act and needs a data processing
    # agreement with every customer that fills them in. Before making either
    # required, ask what the POS actually needs them for — a till does not need
    # a national ID to sell bread, and data not collected cannot leak.
    kra_pin = models.CharField(
        max_length=20,
        blank=True,
        null=True,
    )
    id_number = models.IntegerField(
        null=True,
        blank=True,
    )

    # Professional details
    branch = models.ForeignKey(
        'branches.Branches',
        on_delete=models.PROTECT,
        related_name='organization_staff',
        null=True,
        blank=True,
    )
    start_date = models.DateField(
        null=True,
        blank=True,
    )

    # Timestamps
    created_at = models.DateTimeField(
        default=timezone.now,
    )
    updated_at = models.DateTimeField(
        auto_now=True,
    )

    staff_number = models.CharField(
        max_length=20,
        default=staffNumberGenerator,
        unique=True,
    )

    # Organization
    organization = models.ForeignKey(
        BusinessOrganization,
        on_delete=models.CASCADE,
        related_name='staff',
    )

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Staff Organization'
        verbose_name_plural = 'Staff Organizations'
        constraints = [
            # Unique WITHIN one organisation. A person may work at two shops;
            # nobody is on the same shop's payroll twice.
            models.UniqueConstraint(
                fields=['organization', 'email'],
                name='unique_staff_email_per_organization',
            ),
            models.UniqueConstraint(
                fields=['organization', 'phone_number'],
                name='unique_staff_phone_per_organization',
            ),
            models.UniqueConstraint(
                fields=['organization', 'id_number'],
                name='unique_staff_id_number_per_organization',
            ),
            models.UniqueConstraint(
                fields=['organization', 'kra_pin'],
                name='unique_staff_kra_pin_per_organization',
            ),
        ]


    def __str__(self):
        return self.full_name

