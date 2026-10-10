"""The smallest shop that can take an M-Pesa payment."""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches, Register, RegisterShift, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization, OrganizationStaff

from payments.models import MpesaTill

PASSWORD = "till-password-not-real"

# Obviously fake, and shaped like the real thing so the tests exercise the
# same code paths. Nothing here is a credential.
FAKE_KEY = "not-a-real-consumer-key"
FAKE_SECRET = "not-a-real-consumer-secret"
FAKE_PASSKEY = "not-a-real-passkey"


def a_shop(name="Shop A", *, branches=("Main",)):
    org = BusinessOrganization.objects.create(name=name)
    made = [
        Branches.objects.create(
            organization=org,
            branch_name=f"{name} {branch}",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="A Manager",
            is_active=True,
        )
        for branch in branches
    ]
    return org, made


def a_staff(org, *, name, email, id_number):
    return OrganizationStaff.objects.create(
        organization=org,
        full_name=name,
        email=email,
        phone_number=f"+2547{id_number:08d}",
        address="Nairobi",
        id_number=id_number,
    )


def assign(staff, branch, role="CA"):
    return staffAssignment.objects.create(
        staff_member=staff, branch=branch, staff_assignment=role
    )


def a_till_credential(staff, username):
    credential = StaffCredential(staff=staff, username=username)
    credential.set_password(PASSWORD)
    credential.save()
    return credential


def a_subscriber(org, role=TenantMembership.Role.OWNER, *, number, email=None):
    account = PlatformAccount.objects.create(
        genmars_account_id=number,
        email=email or f"user{number}@example.co.ke",
        full_name=f"User {number}",
    )
    TenantMembership.objects.create(account=account, organization=org, role=role)
    return account


def a_product(org, *, name="Milk", cost="70.00", price="100.00"):
    category, _ = CatalogCategories.objects.get_or_create(
        organization=org, name="General"
    )
    return CatalogCategoryProduct.objects.create(
        organization=org,
        category=category,
        name=name,
        sku=f"SKU-{name}",
        cost_price=Decimal(cost),
        selling_price=Decimal(price),
    )


def a_shift(branch, staff, *, number="T1"):
    register = Register.objects.create(
        branch=branch, name=f"Till {number}", register_number=number
    )
    return RegisterShift.objects.create(
        register=register, operator=staff, opening_cash=Decimal("0.00")
    )


def stocked(branch, product, quantity="100"):
    return BranchInventory.objects.create(
        branch=branch, product=product, quantity=Decimal(quantity)
    )


def an_mpesa_till(org, *, active=True, environment="sandbox", complete=True,
                  transaction_type=MpesaTill.TransactionType.PAYBILL,
                  short_code="174379", store_number=""):
    till = MpesaTill(
        organization=org,
        short_code=short_code,
        store_number=store_number,
        transaction_type=transaction_type,
        environment=environment,
        account_reference="SHOP",
        is_active=active,
    )
    if complete:
        till.set_credentials(
            consumer_key=FAKE_KEY,
            consumer_secret=FAKE_SECRET,
            passkey=FAKE_PASSKEY,
        )
    till.save()
    return till
