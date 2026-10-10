"""
The smallest shop that can buy something.

Shared by the service tests and the HTTP ones, so a change to the model's
required fields breaks in one place rather than in four setUps.
"""

from __future__ import annotations

from decimal import Decimal

from branches.models import Branches, staffAssignment
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity.models import PlatformAccount, StaffCredential, TenantMembership
from organisations.models import BusinessOrganization, OrganizationStaff

from procurement.models import Supplier

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
        # Unique per organisation, so derive it from a number the caller
        # already has to keep distinct.
        phone_number=f"+2547{id_number:08d}",
        address="Nairobi",
        id_number=id_number,
    )


def assign(staff, branch, role):
    return staffAssignment.objects.create(
        staff_member=staff, branch=branch, staff_assignment=role
    )


def a_till(staff, username):
    credential = StaffCredential(staff=staff, username=username)
    credential.set_password(PASSWORD)
    credential.save()
    return credential


def a_subscriber(org, role, *, number, email=None):
    account = PlatformAccount.objects.create(
        genmars_account_id=number, email=email or f"user{number}@example.co.ke"
    )
    TenantMembership.objects.create(account=account, organization=org, role=role)
    return account


def a_product(org, *, name="Milk", cost="70.00", price="100.00",
              units_per_pack="1", pack_name=""):
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
        units_per_pack=Decimal(units_per_pack),
        pack_name=pack_name,
    )


def a_supplier(org, *, name="Brookside", **kwargs):
    return Supplier.objects.create(organization=org, name=name, **kwargs)
