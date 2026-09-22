from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from organisations.models import BusinessOrganization, OrganizationStaff
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
    organization = models.ForeignKey(BusinessOrganization, on_delete=models.CASCADE, related_name='branches', default=1)
    branch_name = models.CharField(max_length=38)
    branch_location = models.CharField(max_length=155)
    branch_allocation = models.CharField(max_length=255)
    # Not globally unique: two shops may both have a manager called John, and
    # refusing the second would say so. (This is a name in a text field where
    # a foreign key to OrganizationStaff belongs — worth fixing, separately,
    # because renaming somebody currently orphans their branch.)
    branch_manager = models.CharField(max_length=60)
    # Generated, and the generator already checks the whole table — so this one
    # is genuinely unique platform-wide and should say so.
    branch_number = models.CharField(
        default=branch_number_generator, max_length=18, unique=True
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=timezone.now)

    def __str__(self):
        return self.branch_name


class Register(models.Model):

    branch = models.ForeignKey(
    'branches.Branches',
    on_delete=models.PROTECT,
    related_name='registers'
)

    # Per branch, not per platform. Every shop calls its first till "Till 1".
    name = models.CharField(max_length=100)
    register_number = models.CharField(max_length=100)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['branch', 'name'], name='unique_register_name_per_branch'
            ),
            models.UniqueConstraint(
                fields=['branch', 'register_number'],
                name='unique_register_number_per_branch',
            ),
        ]

    def __str__(self):
        return self.name

class RegisterShift(models.Model):

    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name='shifts')

    operator = models.ForeignKey(OrganizationStaff, on_delete=models.PROTECT, related_name='register_shifts')

    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    closing_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    status = models.CharField(max_length=20, choices=[('OPEN', 'open'), ('CLOSED', 'closed')], default='OPEN')

    note = models.TextField(
        blank=True,
        default="",
        help_text=(
            "What the person on the till wants the manager to know: a float "
            "taken for change, a jam, a customer coming back. Written during "
            "the shift and frozen when it closes."
        ),
    )

    def __str__(self):
        return f"{self.register} and {self.operator}"

class staffAssignment(models.Model):

    class StaffRoles(models.TextChoices):
        AssistantManager = 'AM', 'Assistant manager'
        Cashier = 'CA', 'Cashier'
        SaleAssociate = 'SA', 'Sales Associate'
        InventoryClerk = 'IC', 'Inventory Clerk'
        PurchasingOfficer = 'PO', 'Purchasing Officer'
        FinanceClerk = 'FC', 'Finance Clerk'
        BranchAuditor = 'BA', 'Branch Auditor'


    staff_member = models.ForeignKey(OrganizationStaff, on_delete=models.CASCADE, related_name='assignments')

    branch = models.ForeignKey('branches.Branches', on_delete=models.PROTECT, related_name='staff_assignments')

    is_active = models.BooleanField(default=True)

    staff_assignment = models.CharField(choices=StaffRoles.choices, default=StaffRoles.Cashier)

    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # Blocks entering the EXACT SAME staff, branch, and role more than once while active
            models.UniqueConstraint(
                fields=['staff_member', 'branch', 'staff_assignment'],
                condition=models.Q(is_active=True),
                name='unique_active_staff_role_per_branch'
            )
        ]

    def clean(self):
        # Validation layer to return a clean error message to the user
        if self.is_active:
            duplicate = staffAssignment.objects.filter(
                staff_member=self.staff_member,
                branch=self.branch,
                staff_assignment=self.staff_assignment,
                is_active=True
            ).exclude(pk=self.pk)
            
            if duplicate.exists():
                raise ValidationError(
                    f"{self.staff_member.full_name} is already assigned as a "
                    f"{self.get_staff_assignment_display()} at {self.branch.branch_name}."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

        
    def __str__(self):
        return f"{self.staff_member} as {self.get_staff_assignment_display()} at {self.branch}"

