"""
One shop must never see, or write into, another.

Written against the HTTP surface rather than against the queryset helpers,
because the claim worth defending is not "scoped() filters correctly" — it is
"a request from Shop A carrying Shop B's id gets nothing".

Blueprint §1: "Organization A must never be able to read or mutate Organization
B's data." Blueprint §8: "Never trust branch_id or organization_id supplied by a
client as proof of authorization."
"""

from __future__ import annotations

from rest_framework.test import APITestCase

from branches.models import Branches, Register
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity import services
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization, OrganizationStaff


class TwoShops(APITestCase):
    """Two tenants, fully furnished, and a subscriber who belongs to one."""

    def setUp(self):
        self.a = BusinessOrganization.objects.create(name="Shop A")
        self.b = BusinessOrganization.objects.create(name="Shop B")

        self.branch_a = Branches.objects.create(
            organization=self.a,
            branch_name="A Central",
            branch_location="Nairobi",
            branch_allocation="main",
            branch_manager="Manager A",
        )
        self.branch_b = Branches.objects.create(
            organization=self.b,
            branch_name="B Central",
            branch_location="Mombasa",
            branch_allocation="main",
            branch_manager="Manager B",
        )

        self.category_b = CatalogCategories.objects.create(
            organization=self.b, name="Beverages"
        )
        self.product_b = CatalogCategoryProduct.objects.create(
            organization=self.b,
            category=self.category_b,
            name="Soda",
            sku="SODA-1",
            cost_price=50,
            selling_price=80,
        )
        self.staff_b = OrganizationStaff.objects.create(
            organization=self.b,
            full_name="Bee Cashier",
            email="bee@shop-b.co.ke",
            phone_number="+254700000002",
            address="Mombasa",
        )

        # Somebody who belongs to Shop A, and only Shop A.
        self.account = PlatformAccount.objects.create(
            genmars_account_id=1, email="owner@shop-a.co.ke"
        )
        TenantMembership.objects.create(account=self.account, organization=self.a)

        session = self.client.session
        session["platform_account_id"] = self.account.pk
        session.save()


class ReadIsolationTests(TwoShops):
    def test_the_organisation_list_shows_only_their_own(self):
        response = self.client.get("/org/organizations/")
        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in _rows(response)]
        self.assertEqual(names, ["Shop A"])

    def test_another_shops_branch_is_not_found_rather_than_forbidden(self):
        """
        404, not 403. A 403 confirms the row exists, which is the same leak
        wearing a different status code.
        """
        response = self.client.get(f"/brn/branch/{self.branch_b.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_another_shops_catalogue_is_invisible(self):
        rows = _rows(self.client.get("/ctl/products/"))
        self.assertEqual(rows, [])

    def test_another_shops_staff_are_invisible(self):
        body = self.client.get("/org/staff/").content.decode()
        self.assertNotIn("bee@shop-b.co.ke", body)
        self.assertNotIn("Bee Cashier", body)

    def test_a_caller_with_no_tenant_sees_nothing(self):
        stranger = PlatformAccount.objects.create(
            genmars_account_id=99, email="nobody@example.invalid"
        )
        session = self.client.session
        session["platform_account_id"] = stranger.pk
        session.save()

        self.assertEqual(_rows(self.client.get("/org/organizations/")), [])
        self.assertEqual(_rows(self.client.get("/brn/branch/")), [])


class WriteIsolationTests(TwoShops):
    """
    The half that scoping a queryset does nothing about.

    A create naming another tenant's branch arrives as a perfectly valid
    foreign key, and the ORM is delighted to save it.
    """

    def test_the_same_create_SUCCEEDS_in_their_own_branch(self):
        """
        The control for the test below.

        Without it, a 400 from the wrong URL, a missing field or a serializer
        quirk would read as "isolation works" — a negative test that passes for
        a reason that has nothing to do with what it claims to prove.
        """
        response = self.client.post(
            "/brn/register/",
            {"branch_id": self.branch_a.pk, "name": "Till 1", "register_number": "R-1"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Register.objects.filter(branch=self.branch_a).count(), 1)

    def test_their_own_branch_reads_back(self):
        """The control for the 404 tests: the endpoint works, it is scoped."""
        response = self.client.get(f"/brn/branch/{self.branch_a.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["branch_name"], "A Central")

    def test_cannot_create_a_register_in_another_shops_branch(self):
        response = self.client.post(
            "/brn/register/",
            {
                "branch_id": self.branch_b.pk,
                "name": "Till 1",
                "register_number": "R-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        # The refusal must be about the BRANCH, not some unrelated validation.
        self.assertIn("branch", response.json(), "the refusal should name the branch")
        self.assertEqual(Register.objects.filter(branch=self.branch_b).count(), 0)

    def test_cannot_put_a_product_in_another_shops_catalogue(self):
        response = self.client.post(
            "/ctl/products/",
            {
                "organization": self.b.pk,
                "category": self.category_b.pk,
                "name": "Smuggled",
                "sku": "SMUG-1",
                "cost_price": "1.00",
                "selling_price": "2.00",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            CatalogCategoryProduct.objects.filter(name="Smuggled").exists()
        )

    def test_cannot_move_stock_into_another_shops_branch(self):
        response = self.client.post(
            "/invt/inventory/",
            {"branch": self.branch_b.pk, "product": self.product_b.pk, "quantity": "5"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BranchInventory.objects.count(), 0)

    def test_cannot_edit_another_shops_branch(self):
        response = self.client.patch(
            f"/brn/branch/{self.branch_b.pk}/",
            {"branch_name": "Renamed by a stranger"},
            format="json",
        )
        # 404 because the queryset never contained it in the first place.
        self.assertEqual(response.status_code, 404)
        self.branch_b.refresh_from_db()
        self.assertEqual(self.branch_b.branch_name, "B Central")

    def test_cannot_delete_another_shops_branch(self):
        response = self.client.delete(f"/brn/branch/{self.branch_b.pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Branches.objects.filter(pk=self.branch_b.pk).exists())


class StockEndpointIsolationTests(TwoShops):
    """
    The endpoints that arrived with the stock work — new surface, and the kind
    that matters: these move quantities and money-adjacent records around.

    Each one carried its own `get_queryset` filtering on
    `organization__staff__external_user_id=user.id`, which could never match a
    real caller. These assert the replacement actually confines them.
    """

    def setUp(self):
        super().setUp()
        from inventory.models import BranchInventory, StockMovement

        # Stock that belongs to Shop B, which the caller must never see.
        self.stock_b = BranchInventory.objects.create(
            branch=self.branch_b, product=self.product_b, quantity=10
        )
        StockMovement.objects.create(
            inventory=self.stock_b,
            movement_type="PURCHASE",
            quantity_before=0,
            quantity_after=10,
        )

    def test_another_shops_stock_is_invisible(self):
        for url in (
            "/invt/inventory/",
            "/invt/stock-movements/",
            "/invt/stock-levels/",
            "/invt/stock-adjustments/",
            "/invt/stock-transfers/",
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(_rows(response), [], f"{url} leaked")

    def test_cannot_record_a_movement_against_another_shops_stock(self):
        response = self.client.post(
            "/invt/stock-movements/",
            {
                "inventory": self.stock_b.pk,
                "movement_type": "ADJUSTMENT",
                "quantity_before": "10",
                "quantity_after": "0",
            },
            format="json",
        )
        self.assertIn(response.status_code, (400, 403), response.content)
        from inventory.models import StockMovement

        self.assertEqual(
            StockMovement.objects.filter(movement_type="ADJUSTMENT").count(), 0
        )


class UnauthenticatedTests(TwoShops):
    def test_every_endpoint_refuses_a_stranger(self):
        """
        Before this increment there was no REST_FRAMEWORK block at all, so DRF
        defaulted to AllowAny and every one of these answered in full.
        """
        self.client.logout()
        self.client.cookies.clear()

        for url in (
            "/org/organizations/",
            "/org/staff/",
            "/brn/branch/",
            "/brn/register/",
            "/ctl/categories/",
            "/ctl/products/",
            "/invt/inventory/",
            "/invt/stock-movements/",
            "/invt/stock-transfers/",
            "/invt/stock-adjustments/",
            "/invt/stock-levels/",
        ):
            with self.subTest(url=url):
                self.assertIn(
                    self.client.get(url).status_code, (401, 403), f"{url} was open"
                )


class UniquenessIsNotAnOracleTests(APITestCase):
    """
    A uniqueness constraint that spans tenants leaks across them: the error
    tells you a value is taken in a shop you cannot see.
    """

    def setUp(self):
        self.a = BusinessOrganization.objects.create(name="Shop A")
        self.b = BusinessOrganization.objects.create(name="Shop B")

    def test_two_shops_may_share_a_name(self):
        BusinessOrganization.objects.create(name="Naivas")
        BusinessOrganization.objects.create(name="Naivas")  # must not raise

    def test_one_person_may_work_at_two_shops(self):
        for org in (self.a, self.b):
            OrganizationStaff.objects.create(
                organization=org,
                full_name="John Mwangi",
                email="john@example.co.ke",
                phone_number="+254700000009",
                address="Nairobi",
                id_number=12345678,
            )
        self.assertEqual(OrganizationStaff.objects.count(), 2)

    def test_one_shop_still_cannot_list_the_same_person_twice(self):
        from django.db.utils import IntegrityError

        OrganizationStaff.objects.create(
            organization=self.a,
            full_name="John Mwangi",
            email="john@example.co.ke",
            phone_number="+254700000009",
            address="Nairobi",
        )
        with self.assertRaises(IntegrityError):
            OrganizationStaff.objects.create(
                organization=self.a,
                full_name="John Mwangi Again",
                email="john@example.co.ke",
                phone_number="+254700000010",
                address="Nairobi",
            )

    def test_every_shop_may_call_its_first_till_till_one(self):
        for org in (self.a, self.b):
            branch = Branches.objects.create(
                organization=org,
                branch_name=f"{org.name} Central",
                branch_location="Nairobi",
                branch_allocation="main",
                branch_manager="A Manager",
            )
            Register.objects.create(branch=branch, name="Till 1", register_number="R-1")
        self.assertEqual(Register.objects.count(), 2)

    def test_every_shop_may_have_a_beverages_category(self):
        for org in (self.a, self.b):
            CatalogCategories.objects.create(organization=org, name="Beverages")
        self.assertEqual(CatalogCategories.objects.count(), 2)


def _rows(response):
    """DRF may or may not be paginating; this test suite does not care."""
    data = response.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data
