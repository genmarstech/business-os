"""
The buying endpoints.

── EVERY STATE CHANGE IS A NAMED ACTION, NOT A PATCH ───────────────────────────

`submit`, `approve`, `cancel` and `receive` are POSTs to actions, and `status`
is read-only in the serialiser. A PATCH that could set `status: "approved"`
would be an approval with no approver, no timestamp and no permission check
distinct from editing a note — which is three quarters of what an approval is.

── AND EVERY ONE OF THEM ASKS TWICE ────────────────────────────────────────────

`permissions` below answers "may this caller do this at all". Each action then
asks again with the order's branch, because one person can be a purchasing
officer at Westlands and an auditor at Karen, and the union of their roles
would let them approve Karen's orders. Same construction as the till's
checkout.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from Business_Platform.reporting import exact, parse_window
from identity import access
from identity.authentication import StaffPrincipal
from identity.scoping import TenantScoped

from . import reports, services
from . import services
from .models import GoodsReceipt, PurchaseOrder, Supplier
from .serializers import (
    CancelSerializer,
    GoodsReceiptSerializer,
    PurchaseOrderSerializer,
    ReceiveSerializer,
    SupplierSerializer,
)


def _refuse(error: DjangoValidationError) -> Response:
    """
    A ProcurementError as a 400, shaped the way DRF shapes its own.

    services raises Django's ValidationError, which DRF does not translate.
    Without this a refused delivery would be a 500, and somebody holding a
    delivery note would be told the system is broken when the real answer is
    "only four of those are still outstanding". sales/views.py carries the
    twin of this function.
    """
    detail = (
        error.message_dict
        if hasattr(error, "message_dict")
        else {"detail": error.messages}
    )
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


def _acting(request):
    """
    Who is doing this, as a row something can point at.

    A till session knows its staff member. A subscriber is a PlatformAccount,
    which is already a row. Anything else attributes to nobody — see the note
    on `services._actor`, and note that `approve_order` refuses that case
    rather than recording an approval by nobody.
    """
    if isinstance(request.user, StaffPrincipal):
        return request.user.staff
    return request.user


class SupplierViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    Who the shop buys from.

    Organisation-level, so no `branch_path`: a supplier is not somebody one
    branch deals with — §7 puts this kind of configuration at the
    organisation, like the catalogue and the tax rules.

    Deliberately no delete beyond what DRF gives: a supplier with orders
    behind it is PROTECTed at the database, so `is_active = false` is how one
    leaves the list. An archived supplier still explains every order that
    names it.
    """

    tenant_path = "organization_id"
    default_permission = access.PURCHASING_MANAGE
    permissions = {
        "list": access.PURCHASING_VIEW,
        "retrieve": access.PURCHASING_VIEW,
    }
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


class PurchaseOrderViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    What was asked for, from whom, and where it stands.
    """

    tenant_path = "organization_id"
    # An order is delivered TO a branch, so a principal confined to branches
    # sees only their own. Without this a clerk at Westlands reads what Karen
    # is buying and what it costs them.
    branch_path = "branch_id"

    default_permission = access.PURCHASING_MANAGE
    permissions = {
        "list": access.PURCHASING_VIEW,
        "retrieve": access.PURCHASING_VIEW,
        "submit": access.PURCHASING_MANAGE,
        "approve": access.PURCHASING_APPROVE,
        # Cancelling an order a supplier may already be loading is the same
        # authority as committing to it in the first place.
        "cancel": access.PURCHASING_APPROVE,
        "receive": access.PURCHASING_RECEIVE,
    }
    queryset = (
        PurchaseOrder.objects.select_related(
            "branch",
            "supplier",
            "raised_by_staff",
            "raised_by_account",
            "approved_by_staff",
            "approved_by_account",
        )
        .prefetch_related("items", "receipts")
        .all()
    )
    serializer_class = PurchaseOrderSerializer

    # ── THE WRITES GO THROUGH services, VIA perform_* ───────────────────────
    #
    # `create` and `update` themselves stay as TenantScoped defines them, so
    # the out-of-scope guard cannot be lost — that guard is why it lives in
    # the action method and not in `perform_create`. These only decide what
    # happens once the write has been allowed.

    def perform_create(self, serializer):
        data = serializer.validated_data
        order = services.raise_order(
            branch=data["branch"],
            supplier=data["supplier"],
            lines=[
                {
                    "product": line["product"],
                    "quantity": line["quantity_ordered"],
                    "unit_cost": line.get("unit_cost"),
                }
                for line in data["items"]
            ],
            actor=_acting(self.request),
            expected_at=data.get("expected_at"),
            note=data.get("note") or "",
            idempotency_key=data.get("idempotency_key") or "",
        )
        serializer.instance = order

    def perform_update(self, serializer):
        order = serializer.instance
        data = serializer.validated_data

        # Draft only, including a PATCH that touches nothing but the note.
        # `replace_lines` says the same thing, and a partial update that sends
        # no lines would never reach it.
        if not order.is_editable:
            raise services.ProcurementError(
                {
                    "status": (
                        "That order has been sent. Cancel it and raise "
                        "another, or receive what arrives."
                    )
                }
            )

        if "items" in data:
            order = services.replace_lines(
                order,
                lines=[
                    {
                        "product": line["product"],
                        "quantity": line["quantity_ordered"],
                        "unit_cost": line.get("unit_cost"),
                    }
                    for line in data["items"]
                ],
            )

        # The header fields a draft may still change. `supplier` and `branch`
        # are not among them: an order addressed to a different supplier, or
        # delivered to a different branch, is a different order, and keeping
        # the number would make two documents look like one.
        order.expected_at = data.get("expected_at", order.expected_at)
        order.note = data.get("note", order.note)
        order.save(update_fields=["expected_at", "note", "updated_at"])
        serializer.instance = order

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except DjangoValidationError as error:
            return _refuse(error)

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except DjangoValidationError as error:
            return _refuse(error)

    def _may_here(self, order, permission) -> bool:
        return access.may(self.request.user, permission, order.branch_id)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Draft → sent to the supplier."""
        order = self.get_object()
        if not self._may_here(order, access.PURCHASING_MANAGE):
            return Response(
                {"branch": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            order = services.submit_order(order)
        except DjangoValidationError as error:
            return _refuse(error)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """
        Commit the business to the money.

        The separation from `submit` is the whole point of the pair: a
        purchasing officer holds PURCHASING_MANAGE and not this, so the order
        they raised and sent is approved by somebody else.
        """
        order = self.get_object()
        if not self._may_here(order, access.PURCHASING_APPROVE):
            return Response(
                {"branch": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            order = services.approve_order(order, actor=_acting(request))
        except DjangoValidationError as error:
            return _refuse(error)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if not self._may_here(order, access.PURCHASING_APPROVE):
            return Response(
                {"branch": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )
        form = CancelSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            order = services.cancel_order(
                order, reason=form.validated_data.get("reason", "")
            )
        except DjangoValidationError as error:
            return _refuse(error)
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        """
        Book in what turned up. The only way procurement moves stock.

        Retry-safe: send the same `idempotency_key` and the original delivery
        comes back rather than a second one being booked in. A delivery
        counted twice is stock the shop thinks it has and does not, which is
        found weeks later by a stock take and blamed on theft.
        """
        order = self.get_object()
        if not self._may_here(order, access.PURCHASING_RECEIVE):
            return Response(
                {"branch": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
            )

        form = ReceiveSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        try:
            receipt = services.receive_goods(
                order,
                lines=[
                    {"item": line["item"], "quantity": line["quantity"]}
                    for line in data["lines"]
                ],
                actor=_acting(request),
                delivery_note=data.get("delivery_note") or "",
                note=data.get("note") or "",
                idempotency_key=data.get("idempotency_key") or "",
            )
        except DjangoValidationError as error:
            return _refuse(error)

        order.refresh_from_db()
        return Response(
            {
                "receipt": GoodsReceiptSerializer(receipt).data,
                "order": self.get_serializer(order).data,
            },
            status=status.HTTP_201_CREATED,
        )


class GoodsReceiptViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Deliveries, read-only by design.

    A receipt is written by `POST /prc/purchase-orders/{id}/receive/` and
    amended by nothing. Editing one after the stock has moved would leave the
    document and the movements it produced disagreeing, and the movements are
    the ones a stock take is reconciled against — §10, the same reasoning that
    makes a Sale read-only.
    """

    tenant_path = "organization_id"
    branch_path = "branch_id"
    permissions = {
        "list": access.PURCHASING_VIEW,
        "retrieve": access.PURCHASING_VIEW,
    }
    default_permission = access.PURCHASING_VIEW
    queryset = (
        GoodsReceipt.objects.select_related(
            "branch",
            "purchase_order",
            "purchase_order__supplier",
            "received_by_staff",
            "received_by_account",
        )
        .prefetch_related("items", "items__order_item")
        .all()
    )
    serializer_class = GoodsReceiptSerializer


class BuyingReportViewSet(viewsets.ViewSet):
    """
    What the shop is spending, and on whom — the buying half of module 12.

    A ViewSet with no queryset, because none of these is a list of rows. Each
    action is an aggregate that resolves its own scope inside
    `procurement.reports` — see the banner there on why a report never takes a
    queryset from a view.

    ── ONE PERMISSION, NOT THE REPORTING PAIR ──────────────────────────────
    `sales.ReportViewSet` splits on REPORTS_BRANCH vs REPORTS_ORGANISATION,
    because §4 and §5 describe two different dashboards over the same
    takings, and a branch manager is meant to be refused the consolidated
    one.

    These are gated on PURCHASING_VIEW instead, and that difference is
    deliberate. What a supplier costs and whether they deliver on time is the
    buyer's own working information — a purchasing officer holds no reporting
    permission at all, and gating this behind one would hide the numbers from
    the only person whose job is to act on them. Nobody is shown more than
    their orders list already shows them: the aggregates are confined by
    `scoped_to_branch` exactly as `PurchaseOrderViewSet` is, so a branch
    manager's spend figures stop where their order list stops.
    """

    def _branch(self, request):
        raw = request.query_params.get("branch")
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

    def check(self, request):
        """Returns an error Response, or None when the caller may proceed."""
        if not access.may(request.user, access.PURCHASING_VIEW, self._branch(request)):
            return Response(
                {"detail": "You do not have permission to do that."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @action(detail=False, methods=["get"])
    def overview(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = parse_window(request)
        return Response(
            exact(reports.overview(request.user, start, end, self._branch(request)))
        )

    @action(detail=False, methods=["get"], url_path="by-supplier")
    def by_supplier(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = parse_window(request)
        return Response(
            exact(
                {
                    "suppliers": reports.by_supplier(
                        request.user, start, end, self._branch(request)
                    )
                }
            )
        )

    @action(detail=False, methods=["get"], url_path="by-product")
    def by_product(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = parse_window(request)
        return Response(
            exact(
                {
                    "products": reports.by_product(
                        request.user, start, end, self._branch(request)
                    )
                }
            )
        )

    @action(detail=False, methods=["get"])
    def outstanding(self, request):
        # Takes no window on purpose — it is a position, not a period. See
        # the banner in reports.py.
        refused = self.check(request)
        if refused is not None:
            return refused
        return Response(
            exact(reports.outstanding(request.user, self._branch(request)))
        )

    @action(detail=False, methods=["get"])
    def reliability(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = parse_window(request)
        return Response(
            exact(
                {
                    "suppliers": reports.supplier_reliability(
                        request.user, start, end, self._branch(request)
                    )
                }
            )
        )
