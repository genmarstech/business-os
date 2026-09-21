"""
Inventory, and one check that belongs to every app.
"""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from branches.models import Branches
from catalog.models import CatalogCategories, CatalogCategoryProduct
from identity.authentication import SUBSCRIBER_SESSION_KEY
from identity.models import PlatformAccount, TenantMembership
from inventory.models import BranchInventory
from organisations.models import BusinessOrganization


class DeclaredFieldsActuallyAppearTests(TestCase):
    """
    ══════════════════════════════════════════════════════════════════════════
    A DECLARED FIELD WHOSE `source` IS WRONG DISAPPEARS SILENTLY.

    DRF's Field.get_attribute catches AttributeError, and for a read_only
    field — which is required=False — raises SkipField. Serializer.to_
    representation catches SkipField and moves on. So a typo in a dotted
    source produces a 200 with the field simply absent: no error, no log, no
    failing test, and a client reading it gets `undefined` for ever.

    `BranchInventorySerializer.branch_name` was `source="branch.name"` and
    Branches has `branch_name`. Every inventory response in the product's
    history was missing the field, and nothing anywhere said so.

    So this walks the source paths structurally rather than listing them: a
    new serialiser is covered the day it is written.
    ══════════════════════════════════════════════════════════════════════════
    """

    def serializers(self):
        import importlib
        import inspect

        from rest_framework import serializers as drf

        found = []
        for app in (
            "branches",
            "catalog",
            "identity",
            "inventory",
            "organisations",
            "sales",
        ):
            try:
                module = importlib.import_module(f"{app}.serializers")
            except ModuleNotFoundError:
                continue
            for _, cls in inspect.getmembers(module, inspect.isclass):
                if not issubclass(cls, drf.Serializer):
                    continue
                if getattr(getattr(cls, "Meta", None), "model", None) is None:
                    continue
                found.append(cls)
        return found

    def test_the_walk_finds_something(self):
        """The control. An empty list would pass every assertion below."""
        self.assertGreaterEqual(len(self.serializers()), 8)

    def test_every_dotted_source_resolves_on_its_model(self):
        from django.core.exceptions import FieldDoesNotExist

        broken = []
        for cls in self.serializers():
            model = cls.Meta.model
            for name, field in cls().fields.items():
                source = field.source or name
                if "." not in source:
                    continue

                current = model
                for step in source.split("."):
                    if current is None:
                        break
                    try:
                        meta_field = current._meta.get_field(step)
                    except (FieldDoesNotExist, AttributeError):
                        # Not a column — a property or method is legitimate,
                        # so the check is only that the NAME exists at all.
                        if not hasattr(current, step):
                            broken.append(
                                f"{cls.__name__}.{name} → {source} "
                                f"(no {step!r} on {current.__name__})"
                            )
                        current = None
                        break
                    current = getattr(meta_field, "related_model", None)

        self.assertEqual(
            broken,
            [],
            "these serialiser fields name something that does not exist, and "
            "DRF drops them from the response without a word: " + str(broken),
        )


class InventoryOverHttpTests(TestCase):
    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Shop A")
        self.branch = Branches.objects.create(
            organization=self.org,
            branch_name="Westlands",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="A Manager",
            is_active=True,
        )
        category = CatalogCategories.objects.create(
            organization=self.org, name="General"
        )
        product = CatalogCategoryProduct.objects.create(
            organization=self.org,
            category=category,
            name="Milk",
            sku="SKU-1",
            cost_price=Decimal("70.00"),
            selling_price=Decimal("100.00"),
        )
        BranchInventory.objects.create(
            branch=self.branch, product=product, quantity=Decimal("50")
        )

        account = PlatformAccount.objects.create(
            genmars_account_id=9001, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(
            account=account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = account.pk
        session.save()

    def test_a_stock_row_names_its_branch(self):
        """
        The concrete case behind the structural test above: the field was
        declared, the endpoint answered 200, and the name was never there.
        """
        response = self.client.get("/invt/inventory/")
        self.assertEqual(response.status_code, 200)

        rows = response.json()
        rows = rows if isinstance(rows, list) else rows["results"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["branch_name"], "Westlands")
        self.assertEqual(rows[0]["product_name"], "Milk")


class AdjustingStockTests(TestCase):
    """
    Booking stock in and out, and the trail it leaves.

    Every test here checks the MOVEMENT as well as the number. A quantity that
    changed with nothing saying why is the failure this endpoint exists to
    prevent, and a test that only asserts the total would pass over it.
    """

    def setUp(self):
        self.org = BusinessOrganization.objects.create(name="Shop A")
        self.branch = Branches.objects.create(
            organization=self.org,
            branch_name="Westlands",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="A Manager",
            is_active=True,
        )
        category = CatalogCategories.objects.create(
            organization=self.org, name="General"
        )
        self.product = CatalogCategoryProduct.objects.create(
            organization=self.org,
            category=category,
            name="Milk",
            sku="SKU-1",
            cost_price=Decimal("70.00"),
            selling_price=Decimal("100.00"),
        )
        self.stock = BranchInventory.objects.create(
            branch=self.branch, product=self.product, quantity=Decimal("10")
        )

        self.account = PlatformAccount.objects.create(
            genmars_account_id=9101, email="owner@a.co.ke"
        )
        TenantMembership.objects.create(
            account=self.account,
            organization=self.org,
            role=TenantMembership.Role.OWNER,
        )
        session = self.client.session
        session[SUBSCRIBER_SESSION_KEY] = self.account.pk
        session.save()

    def adjust(self, **body):
        return self.client.post(
            f"/invt/inventory/{self.stock.pk}/adjust/",
            body,
            content_type="application/json",
        )

    def test_a_delivery_goes_in_and_leaves_a_movement(self):
        from inventory.models import StockAdjustment, StockMovement

        response = self.adjust(quantity="24", reason="DELIVERY", note="Friday drop")
        self.assertEqual(response.status_code, 201, response.content)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("34.00"))

        movement = StockMovement.objects.get(inventory=self.stock)
        self.assertEqual(movement.movement_type, "PURCHASE")
        self.assertEqual(movement.quantity_before, Decimal("10.00"))
        self.assertEqual(movement.quantity_after, Decimal("34.00"))

        adjustment = StockAdjustment.objects.get(inventory=self.stock)
        self.assertEqual(adjustment.adjustment_type, "INCREASE")
        self.assertEqual(adjustment.reason, "Friday drop")

    def test_breakage_comes_out(self):
        response = self.adjust(quantity="-3", reason="DAMAGE", note="Dropped a crate")
        self.assertEqual(response.status_code, 201, response.content)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("7.00"))

    def test_a_shelf_cannot_hold_less_than_nothing(self):
        """
        A negative here would make every report downstream wrong in a way
        nobody would trace back to this screen.
        """
        response = self.adjust(quantity="-11", reason="COUNT")
        self.assertEqual(response.status_code, 400, response.content)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.00"))

    def test_nothing_is_written_when_the_adjustment_is_refused(self):
        """
        The refusal above must leave no movement behind. A rejected adjustment
        that still wrote a row would put an unexplained entry in the one trail
        that is supposed to explain everything.
        """
        from inventory.models import StockAdjustment, StockMovement

        self.adjust(quantity="-11", reason="COUNT")
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(StockAdjustment.objects.count(), 0)

    def test_a_reason_is_required_and_must_be_one_of_ours(self):
        for reason in ("", "SALE", "TRANSFER_IN", "whatever"):
            response = self.adjust(quantity="5", reason=reason)
            self.assertEqual(
                response.status_code, 400, f"{reason!r} was accepted"
            )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal("10.00"))

    def test_zero_is_not_an_adjustment(self):
        self.assertEqual(self.adjust(quantity="0", reason="COUNT").status_code, 400)

    def test_another_shops_stock_is_not_found(self):
        """
        404, not 403 — confirming the row exists is the enumeration oracle
        identity/scoping.py refuses to give anywhere else.
        """
        other = BusinessOrganization.objects.create(name="Shop B")
        branch = Branches.objects.create(
            organization=other,
            branch_name="Theirs",
            branch_location="Nairobi",
            branch_allocation="Ground floor",
            branch_manager="Somebody",
            is_active=True,
        )
        category = CatalogCategories.objects.create(
            organization=other, name="General"
        )
        product = CatalogCategoryProduct.objects.create(
            organization=other, category=category, name="Theirs", sku="SKU-9",
            cost_price=Decimal("1.00"), selling_price=Decimal("2.00"),
        )
        theirs = BranchInventory.objects.create(
            branch=branch, product=product, quantity=Decimal("5")
        )

        response = self.client.post(
            f"/invt/inventory/{theirs.pk}/adjust/",
            {"quantity": "100", "reason": "DELIVERY"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404, response.content)

        theirs.refresh_from_db()
        self.assertEqual(theirs.quantity, Decimal("5.00"))
