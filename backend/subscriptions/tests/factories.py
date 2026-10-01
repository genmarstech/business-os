"""The smallest tenant that can have a subscription."""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches, Register, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from organisations.models import BusinessOrganization, OrganizationStaff

from subscriptions.models import Plan

PASSWORD = "till-password-not-real"


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


def a_till(staff, username):
    credential = StaffCredential(staff=staff, username=username)
    credential.set_password(PASSWORD)
    credential.save()
    return credential


def a_register(branch, *, name="Till 1", number="T1"):
    return Register.objects.create(branch=branch, name=name, register_number=number)


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


def a_plan(code="standard", **kwargs):
    """
    A plan with no ceilings unless the test asks for one.

    The ceilings are what most of these tests are about, so a default of
    "unlimited" means every limit in a test is one the test put there.
    """
    return Plan.objects.create(
        code=code,
        name=kwargs.pop("name", code.title()),
        monthly_price=Decimal(kwargs.pop("monthly_price", "0.00")),
        **kwargs,
    )
