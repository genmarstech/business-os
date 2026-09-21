"""
The till's endpoints.

── EVERY WRITE GOES THROUGH services.py ────────────────────────────────────────

Sales, refunds and voids are POSTed to actions that validate shape here and
then hand the work to `sales.services`. None of them is a ModelViewSet `create`,
because none of them is "write one row" — see the note at the top of
serializers.py about what a writable `total` would mean.

── AND EVERY READ GOES THROUGH TenantScoped ────────────────────────────────────

Including the ones that look harmless. A sale carries what a customer bought,
what they paid and what they owe; `Sale.objects.all()` in a list view is the
whole platform's trading history in one request.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog.models import TaxRule
from identity.permissions import tenant_scope
from identity.scoping import TenantScoped

from . import services
from .models import Customer, Refund, Sale
from .serializers import (
    CheckoutSerializer,
    CustomerSerializer,
    ReceiptSerializer,
    RefundRequestSerializer,
    RefundSerializer,
    ReprintSerializer,
    SaleSerializer,
    TaxRuleSerializer,
    VoidSerializer,
)


def _refuse(error: DjangoValidationError) -> Response:
    """
    Turn a SaleError into a 400 shaped the way DRF shapes its own.

    services raises Django's ValidationError, which DRF does not translate by
    itself. Without this a refused checkout would be a 500, and a cashier
    would be told the system is broken when the real answer is "only two of
    those left".
    """
    detail = (
        error.message_dict
        if hasattr(error, "message_dict")
        else {"detail": error.messages}
    )
    return Response(detail, status=status.HTTP_400_BAD_REQUEST)


class TaxRuleViewSet(TenantScoped, viewsets.ModelViewSet):
    tenant_path = "organization_id"
    queryset = TaxRule.objects.all()
    serializer_class = TaxRuleSerializer


class CustomerViewSet(TenantScoped, viewsets.ModelViewSet):
    tenant_path = "organization_id"
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer


class SaleViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Read-only by design.

    ReadOnlyModelViewSet rather than ModelViewSet with the write methods
    removed: a sale is written by `checkout` and amended by nothing. There is
    no PATCH on a financial record here, and the absence is the feature —
    blueprint §10.
    """

    tenant_path = "organization_id"
    queryset = (
        Sale.objects.select_related(
            "branch", "register", "shift", "cashier", "customer", "receipt"
        )
        .prefetch_related("items", "payments", "refunds")
        .all()
    )
    serializer_class = SaleSerializer

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """
        Cart → sale. The one endpoint a till cannot work without.

        Retry-safe: send the same `idempotency_key` and the original sale
        comes back instead of a second charge (blueprint §11).
        """
        form = CheckoutSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        # ── scope, BEFORE anything is priced ────────────────────────────────
        #
        # The serialiser resolved `shift`, `cashier` and every product out of
        # unfiltered querysets, which is what a PrimaryKeyRelatedField does.
        # That is fine as far as it goes — this is where it stops going. The
        # message is the same one the isolation layer uses everywhere: a
        # record that is not yours reads exactly like a record that is not
        # there.
        allowed = tenant_scope(request.user)
        shift = data["shift"]
        organisation_id = shift.register.branch.organization_id
        if organisation_id not in allowed:
            return Response(
                {"shift": "No such record, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for field in ("cashier", "customer"):
            value = data.get(field)
            if value is not None and value.organization_id not in allowed:
                return Response(
                    {field: "No such record, or it is not available to you."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            sale = services.checkout(
                shift=shift,
                cashier=data["cashier"],
                customer=data.get("customer"),
                lines=[
                    {
                        "product": line["product"],
                        "quantity": line["quantity"],
                        "discount": line.get("discount") or 0,
                    }
                    for line in data["lines"]
                ],
                payments=[
                    {
                        "method": p["method"],
                        "amount": p["amount"],
                        "reference": p.get("reference", ""),
                    }
                    for p in data["payments"]
                ],
                idempotency_key=data.get("idempotency_key", ""),
            )
        except DjangoValidationError as error:
            return _refuse(error)

        return Response(
            self.get_serializer(sale).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Cancel a sale outright and put the stock back."""
        sale = self.get_object()
        form = VoidSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            services.void_sale(sale, reason=form.validated_data["reason"])
        except DjangoValidationError as error:
            return _refuse(error)
        sale.refresh_from_db()
        return Response(self.get_serializer(sale).data)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        """Give money back against this sale, without altering it."""
        sale = self.get_object()
        form = RefundRequestSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        allowed = tenant_scope(request.user)
        if data["branch"].organization_id not in allowed:
            return Response(
                {"branch": "No such record, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if data["processed_by"].organization_id not in allowed:
            return Response(
                {"processed_by": "No such record, or it is not available to you."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            refund = services.refund_sale(
                sale=sale,
                branch=data["branch"],
                processed_by=data["processed_by"],
                shift=data.get("shift"),
                lines=[
                    {
                        "sale_item": line["sale_item"],
                        "quantity": line["quantity"],
                        "restock": line.get("restock", True),
                    }
                    for line in data["lines"]
                ],
                reason=data["reason"],
                idempotency_key=data.get("idempotency_key", ""),
            )
        except DjangoValidationError as error:
            return _refuse(error)

        return Response(
            RefundSerializer(refund).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def reprint(self, request, pk=None):
        """
        Hand the receipt over again, and record that it happened. See the note
        on the Receipt model for why the count lives here and not at the till.
        """
        sale = self.get_object()
        receipt = services.issue_receipt(sale)
        form = ReprintSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        with transaction.atomic():
            receipt = services.reprint_receipt(
                receipt, delivered_to=form.validated_data.get("delivered_to", "")
            )
        return Response(ReceiptSerializer(receipt).data)


class RefundViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Read-only for the same reason as SaleViewSet. Refunds are created through
    `POST /sls/sales/{id}/refund`, where the sale being reversed is part of
    the address rather than a field somebody can point elsewhere.
    """

    tenant_path = "organization_id"
    queryset = (
        Refund.objects.select_related("sale", "branch", "processed_by")
        .prefetch_related("items")
        .all()
    )
    serializer_class = RefundSerializer
