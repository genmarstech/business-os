"""
Inventory.

── SCOPING NOTE, 2026-09-21 ────────────────────────────────────────────────────

Each viewset here used to carry its own `get_queryset` filtering on
`...organization__staff__external_user_id=user.id`. Those were replaced with
the shared `TenantScoped` mixin, for three reasons and not because there was
anything wrong with the intent:

  · `external_user_id` defaults to `uuid.uuid4()`, so it is generated HERE and
    never equals an id from anywhere else. The filter could not match a real
    caller, and the join it walked — organisation to staff to a uuid — is not
    the question being asked anyway.
  · `request.user` is now a `PlatformAccount` or a `StaffPrincipal`
    (identity/authentication.py). Neither has an `id` that is a UUID.
  · Five copies of a filter is five places for the sixth one to be forgotten.
    `identity/scoping.py` is one file, and it guards writes as well as reads —
    scoping a queryset does nothing about a create that names another shop's
    branch.

Their `select_related` choices are kept as written; they were right.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from identity import access
from identity.permissions import acting
from identity.authentication import StaffPrincipal
from identity.permissions import tenant_scope
from identity.scoping import TenantScoped

from . import services

from .models import (
    BranchInventory,
    StockMovement,
    StockTransfer,
    StockAdjustment,
    StockCount,
    StockLevel,
)

from .serializers import (
    BranchInventorySerializer,
    StockMovementSerializer,
    StockTransferSerializer,
    StockAdjustmentSerializer,
    StockCountLineSerializer,
    StockCountSerializer,
    StockLevelSerializer,
    OpenCountSerializer,
    RecordCountSerializer,
    AbandonCountSerializer,
)


# ---------------------------------------------------------
# Greetings
# ---------------------------------------------------------

@api_view(["GET"])
def greetings(request):
    return Response({
        "message": "Hello from the inventory application"
    })


def _refused(error) -> dict:
    """A Django ValidationError in the shape DRF returns everywhere else."""
    return (
        error.message_dict
        if hasattr(error, "message_dict")
        else {"detail": error.messages}
    )





# ---------------------------------------------------------
# Branch Inventory
# ---------------------------------------------------------

class BranchInventoryViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "branch__organization_id"
    branch_path = "branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
        # Named rather than inherited. It happens to match the default, which
        # is exactly the situation that makes an unnamed action look fine
        # until somebody changes the default — see
        # EveryCustomActionIsNamedTests.
        "adjust": access.INVENTORY_ADJUST,
    }
    queryset = BranchInventory.objects.select_related(
        'branch', 'product'
    ).all()
    serializer_class = BranchInventorySerializer

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        """
        Change a quantity, with the reason that explains it.

        ══════════════════════════════════════════════════════════════════════
        THIS IS THE ONLY WAY A QUANTITY CHANGES OUTSIDE A SALE.

        PATCHing `quantity` on this viewset would move stock with nothing
        saying why, and stock is the one figure in a shop that cannot be
        reconstructed from anything else — a shelf count is only ever
        explained by the movements that produced it.

        ⚠ `POST /invt/stock-adjustments/` CANNOT DO THIS AND NEVER COULD.
          quantity_before and quantity_after are read-only there, correctly —
          a client that could state the "before" could state a false one — and
          nothing computed them, so the create could only fail. A shop could
          sell its stock down and had no way to book a delivery back in.
        ══════════════════════════════════════════════════════════════════════
        """
        inventory = self.get_object()

        try:
            delta = services.as_quantity(request.data.get("quantity"))
            adjustment = services.adjust(
                inventory=inventory,
                delta=delta,
                reason=str(request.data.get("reason", "")).upper(),
                note=str(request.data.get("note", "")).strip(),
            )
        except DjangoValidationError as error:
            detail = (
                error.message_dict
                if hasattr(error, "message_dict")
                else {"detail": error.messages}
            )
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)

        inventory.refresh_from_db()
        return Response(
            {
                "inventory": self.get_serializer(inventory).data,
                "adjustment": StockAdjustmentSerializer(adjustment).data,
            },
            status=status.HTTP_201_CREATED,
        )



# ---------------------------------------------------------
# Stock Movement
# ---------------------------------------------------------

class StockMovementViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockMovement.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockMovementSerializer



# ---------------------------------------------------------
# Stock Transfer
# ---------------------------------------------------------

class StockTransferViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "from_branch__organization_id"
    branch_path = "from_branch_id"
    default_permission = access.INVENTORY_TRANSFER
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockTransfer.objects.select_related(
        'from_branch', 'to_branch', 'product'
    ).all()
    serializer_class = StockTransferSerializer



# ---------------------------------------------------------
# Stock Adjustment
# ---------------------------------------------------------

class StockAdjustmentViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockAdjustment.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockAdjustmentSerializer



# ---------------------------------------------------------
# Stock Level
# ---------------------------------------------------------

class StockLevelViewSet(TenantScoped, viewsets.ModelViewSet):

    tenant_path = "inventory__branch__organization_id"
    branch_path = "inventory__branch_id"
    default_permission = access.INVENTORY_ADJUST
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
    }
    queryset = StockLevel.objects.select_related(
        'inventory', 'inventory__branch', 'inventory__product'
    ).all()
    serializer_class = StockLevelSerializer




# ---------------------------------------------------------
# Stock counts
# ---------------------------------------------------------


class StockCountViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Counting a branch's shelves, and booking what the count found.

    ══════════════════════════════════════════════════════════════════════════
    READ-ONLY AS A MODELVIEWSET, FOR THE REASON BranchInventory IS.

    There is no POST, PATCH or DELETE on a count. A count is opened, lines are
    recorded, and it is closed — three operations with rules, not three shapes
    of row. A writable serialiser here would let a client state its own
    `expected_quantity`, which is the one figure in a stock take that must
    come from the system rather than from whoever is holding the clipboard.
    ══════════════════════════════════════════════════════════════════════════
    """

    tenant_path = "organization_id"
    branch_path = "branch_id"
    default_permission = access.INVENTORY_COUNT
    permissions = {
        "list": access.INVENTORY_VIEW,
        "retrieve": access.INVENTORY_VIEW,
        "open": access.INVENTORY_COUNT,
        "record": access.INVENTORY_COUNT,
        # Named separately and deliberately different — see the note beside
        # INVENTORY_COUNT_CLOSE in identity/access.py. The person who counted
        # the shelf is the last one who should settle the shortfall alone.
        "close": access.INVENTORY_COUNT_CLOSE,
        "abandon": access.INVENTORY_COUNT_CLOSE,
    }
    queryset = (
        StockCount.objects.select_related(
            "branch",
            "opened_by_staff",
            "opened_by_account",
            "closed_by_staff",
            "closed_by_account",
        )
        .prefetch_related(
            "lines__inventory__product",
            "lines__counted_by_staff",
            "lines__counted_by_account",
        )
        .all()
    )
    serializer_class = StockCountSerializer

    @action(detail=False, methods=["post"])
    def open(self, request):
        """
        Begin counting a branch.

        ══════════════════════════════════════════════════════════════════════
        THE ONLY WRITE HERE THAT NAMES A BRANCH, SO THE ONLY ONE THAT HAS TO
        CHECK ONE.

        `TenantScoped` guards writes in `create()` and `update()`. A custom
        action reaches neither, and this one shipped without the check: a
        `branch` resolved from an unfiltered queryset meant Shop A could open
        a count inside Shop B — taking B's one permitted open count with it,
        so B could not start their own, against a branch A cannot even read.
        Blueprint §1 and §8, both.

        `record`, `close` and `abandon` are `detail=True`: the count arrives
        through `get_object()`, which is scoped, and `record_count` refuses
        an inventory row from any other branch. This one had nothing.
        ══════════════════════════════════════════════════════════════════════
        """
        form = OpenCountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        branch = form.validated_data["branch"]

        # Tenant first, then branch — two different questions, and the order
        # matters for the reason payments/views.py sets out at length: for a
        # subscriber `branch_scope` is None, so `may(..., branch_id)` would
        # happily answer yes about another business's branch. It was never
        # asked which business.
        if branch.organization_id not in tenant_scope(request.user):
            return Response(
                {"branch": "No such record, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # And now whether they may count THERE, not merely somewhere. A clerk
        # assigned to Westlands holds `inventory.count`; that is not consent
        # to start a stock take at Karen.
        if not access.may(request.user, access.INVENTORY_COUNT, branch.pk):
            return Response(
                {"detail": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            count = services.open_count(
                branch=branch,
                actor=acting(request),
                note=form.validated_data.get("note", ""),
            )
        except DjangoValidationError as error:
            return Response(_refused(error), status=status.HTTP_400_BAD_REQUEST)
        return Response(
            self.get_serializer(count).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        """What is actually on one shelf. Nothing moves until the count closes."""
        count = self.get_object()
        form = RecordCountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            line = services.record_count(
                count=count,
                inventory=form.validated_data["inventory"],
                counted=form.validated_data["counted"],
                actor=acting(request),
                note=form.validated_data.get("note", ""),
            )
        except DjangoValidationError as error:
            return Response(_refused(error), status=status.HTTP_400_BAD_REQUEST)
        return Response(StockCountLineSerializer(line).data)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        """Book every variance and close the count. Nothing to validate —
        who is signing comes from the session, not the body."""
        count = self.get_object()
        try:
            closed, applied = services.close_count(
                count=count, actor=acting(request)
            )
        except DjangoValidationError as error:
            return Response(_refused(error), status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"count": self.get_serializer(closed).data, "adjustments": applied}
        )

    @action(detail=True, methods=["post"])
    def abandon(self, request, pk=None):
        count = self.get_object()
        form = AbandonCountSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            abandoned = services.abandon_count(
                count=count,
                actor=acting(request),
                reason=form.validated_data["reason"],
            )
        except DjangoValidationError as error:
            return Response(_refused(error), status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(abandoned).data)
