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



class CashMovement(models.Model):
    """
    Cash in or out of a drawer, between opening it and counting it.

    ══════════════════════════════════════════════════════════════════════════
    WITHOUT THIS, THE VARIANCE LIES ON ANY DAY SOMEBODY BANKS THE TAKINGS.

    Expected cash was opening float plus cash taken less change given. A shop
    that lifts KSh 5,000 out at lunchtime to walk it to the bank then counts a
    drawer 5,000 below what the system expects, and the till reports it SHORT
    — for doing the single most ordinary thing a cash business does.

    The product already knew. `RegisterShift.note` says, in its own help text,
    "a float taken for change" — so the answer until now was prose in a box
    nothing adds up. And since short drawers raise a notification to every
    holder of `reports.branch`, the cost was not merely a wrong figure: it was
    a manager paged, every day, about money nobody lost.
    ══════════════════════════════════════════════════════════════════════════

    ── IN AND OUT ARE NOT THE SAME AUTHORITY, AND THE ARITHMETIC IS WHY ──────

    A PAY-IN raises the expected figure. Recording a false one makes the
    drawer look MORE short, never less, so it cannot hide anything — a cashier
    fetching change from the safe may record it themselves.

    A PAY-OUT lowers it. A cashier who could record one could take money and
    write the shortfall away in the same movement, which is precisely the
    control a drawer count exists to be. So it needs somebody else:
    `shift.close`, held by a branch manager and deliberately not by a cashier
    — the same shape as SALES_VOID at the till and PURCHASING_APPROVE on an
    order. The second person is a permission the first one does not hold.

    ── IT IS APPEND-ONLY, LIKE EVERY OTHER MOVEMENT HERE ────────────────────

    No edit and no delete. A movement recorded wrongly is corrected by a
    second movement in the opposite direction, for the reason blueprint §10
    gives about sales: a figure that can be rewritten after the fact is a
    figure that explains nothing at an audit. `ActivityLog` in gen-portal and
    `StockMovement` in this application both work this way.
    """

    class Kind(models.TextChoices):
        # Money arriving. Both raise the expected figure.
        FLOAT_IN = "float_in", "Change brought in"
        # Money leaving. Both lower it, and both need shift.close.
        SAFE_DROP = "safe_drop", "Dropped to the safe or banked"
        PAY_OUT = "pay_out", "Paid out of the drawer"

    # The three above, split by direction once so no caller has to remember
    # which is which. A new kind must be added to exactly one of these sets,
    # and `direction()` raises if it is in neither — a kind with no direction
    # would silently count as nothing in the expected figure.
    INWARD = frozenset({Kind.FLOAT_IN})
    OUTWARD = frozenset({Kind.SAFE_DROP, Kind.PAY_OUT})

    shift = models.ForeignKey(
        RegisterShift, on_delete=models.CASCADE, related_name="cash_movements"
    )

    kind = models.CharField(max_length=16, choices=Kind.choices)

    # Always positive. The direction is the kind, not the sign — a signed
    # amount means every report has to remember which way round it is, and
    # one of them will not.
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    # Required, and not defaulted. "Why is there 5,000 less in this drawer"
    # is the entire question this row exists to answer, and a blank reason
    # makes the row a worse version of no row at all.
    reason = models.CharField(max_length=200)

    # Who moved it. Both nullable and exactly one set, the same shape the
    # count and the purchase order use: a subscriber has no OrganizationStaff
    # row and never will.
    recorded_by_staff = models.ForeignKey(
        "organisations.OrganizationStaff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_movements",
    )
    recorded_by_account = models.ForeignKey(
        "identity.PlatformAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cash_movements",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["shift", "created_at"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.amount}"

    @property
    def is_inward(self) -> bool:
        return self.kind in self.INWARD

    @property
    def signed_amount(self):
        """What this does to the expected figure."""
        return self.amount if self.is_inward else -self.amount
