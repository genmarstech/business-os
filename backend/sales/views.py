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

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from catalog.models import TaxRule
from identity import access
from identity.authentication import StaffPrincipal
from identity.permissions import tenant_scope
from identity.scoping import TenantScoped

from . import reports, services
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


def _exact(value):
    """
    Render a report's Decimals as strings, all the way down.

    ── DRF RENDERS A BARE Decimal AS A float ───────────────────────────────
    Its JSON encoder does `float(obj)`, so 1234.55 leaves here as 1234.55 and
    19.99 leaves as 19.989999999999998. DRF's own DecimalField does not have
    this problem — it emits a string — but these reports are plain dicts, not
    serialisers, so they miss that treatment entirely.

    Money that has been through a float is money that no longer adds up, and a
    dashboard whose total disagrees with the sum of its rows by a cent is a
    dashboard nobody trusts again. So the conversion is explicit and happens
    once, here, on the way out.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _exact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_exact(item) for item in value]
    return value


class TaxRuleViewSet(TenantScoped, viewsets.ModelViewSet):
    """
    Tax configuration is organisation-owned (§7). Writing is held at
    SETTINGS_TAX — an owner or an org admin, because a VAT rate change is
    ordinary work that should not need the owner fetched — while anybody who
    can see a price can see the rule behind it.

    Changing a rate is not retroactive: TaxRule.rate is copied onto every
    SaleItem at the moment of sale, so nothing already charged moves.
    """

    tenant_path = "organization_id"
    queryset = TaxRule.objects.all()
    serializer_class = TaxRuleSerializer
    default_permission = access.SETTINGS_TAX
    permissions = {
        "list": access.CATALOG_VIEW,
        "retrieve": access.CATALOG_VIEW,
    }


class CustomerViewSet(TenantScoped, viewsets.ModelViewSet):
    tenant_path = "organization_id"
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    default_permission = access.CUSTOMER_MANAGE
    permissions = {
        "list": access.CUSTOMER_VIEW,
        "retrieve": access.CUSTOMER_VIEW,
    }


class SaleViewSet(TenantScoped, viewsets.ReadOnlyModelViewSet):
    """
    Read-only by design.

    ReadOnlyModelViewSet rather than ModelViewSet with the write methods
    removed: a sale is written by `checkout` and amended by nothing. There is
    no PATCH on a financial record here, and the absence is the feature —
    blueprint §10.
    """

    tenant_path = "organization_id"
    # A sale happens AT a branch, so a principal confined to branches sees
    # only their own. Without this a cashier at Westlands reads Karen's
    # trading history, which is the hole §8 is about.
    branch_path = "branch_id"
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
        "checkout": access.SALES_CHECKOUT,
        "void": access.SALES_VOID,
        "refund": access.SALES_REFUND,
        "reprint": access.SALES_REPRINT,
    }
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

        # ── A TILL RINGS UP AS ITSELF AND AS NOBODY ELSE ────────────────────
        #
        # `cashier` arrives from the client, and the check above only asks
        # whether that person works for the same shop. Every colleague does.
        # So a cashier could put their own takings under somebody else's name
        # — and `Sale.cashier` is precisely the field a drawer is reconciled
        # against and a disputed transaction traced through. A discrepancy
        # that can be attributed to whoever is on shift next is not an
        # attribution at all.
        #
        # A SUBSCRIBER may still name anybody in their tenant: an owner
        # entering a sale on a cashier's behalf is ordinary, and they hold the
        # organisation-wide authority that makes it their call. The rule is
        # about the tier that does not.
        if isinstance(request.user, StaffPrincipal):
            if data["cashier"].pk != request.user.staff.pk:
                return Response(
                    {"cashier": "A till records the sale against whoever is "
                                "signed in to it."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # ── AND NOW THE BRANCH-LEVEL QUESTION ───────────────────────────────
        #
        # `permissions` above asked whether this caller may check out AT ALL.
        # Here the shift has told us where, so the question becomes whether
        # they may check out THERE — which is different for anybody holding
        # two assignments. A cashier at Westlands and manager at Karen must
        # not be able to take a sale at Karen's till on the strength of the
        # Westlands one, and the union of their roles would let them.
        branch_id = shift.register.branch_id
        if not access.may(request.user, access.SALES_CHECKOUT, branch_id):
            return Response(
                {"shift": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
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

        # The same rule as `checkout`, and it matters more here. Money leaving
        # the drawer under somebody else's name is the shape a till theft
        # takes; `Refund.processed_by` is the only record of who authorised
        # it.
        if isinstance(request.user, StaffPrincipal):
            if data["processed_by"].pk != request.user.staff.pk:
                return Response(
                    {"processed_by": "A refund is recorded against whoever is "
                                     "signed in."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        # Refunding at a branch needs the permission AT that branch — see the
        # note in `checkout`. The branch arrives in the request, so this is
        # also the point §8 is warning about: it narrows what happens, and the
        # authority to do it comes from the assignment, not from the field.
        if not access.may(request.user, access.SALES_REFUND, data["branch"].pk):
            return Response(
                {"branch": "You are not assigned to that branch."},
                status=status.HTTP_403_FORBIDDEN,
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
    branch_path = "branch_id"
    permissions = {
        "list": access.SALES_VIEW,
        "retrieve": access.SALES_VIEW,
    }
    queryset = (
        Refund.objects.select_related("sale", "branch", "processed_by")
        .prefetch_related("items")
        .all()
    )
    serializer_class = RefundSerializer


class ReportViewSet(viewsets.ViewSet):
    """
    The dashboard and the reports — blueprint modules 11 and 12.

    A ViewSet with no queryset, because none of these is a list of rows. Each
    action is an aggregate, and every one of them resolves its own scope
    through `sales.reports` rather than taking a queryset from here — see the
    banner in that module on why a report is the easiest place to leak a whole
    platform at once.

    `branch` narrows any of them; it is NOT trusted as authority. A branch the
    caller cannot see produces an empty report rather than a refusal, because
    the aggregate is taken over already-scoped sales and a branch outside the
    scope simply matches nothing — §8's "never trust branch_id supplied by a
    client as proof of authorization", arranged so that obeying it needs no
    extra check.
    """

    def _branch(self, request):
        raw = request.query_params.get("branch")
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

    # ── THE TWO REPORTING PERMISSIONS ───────────────────────────────────────
    #
    # §4 and §5 describe two different dashboards: the organisation's command
    # centre, and a branch's own operational view where a manager "should see
    # only the data and actions permitted for that branch".
    #
    # So a request that names no branch is an organisation-wide question and
    # needs REPORTS_ORGANISATION; one that names a branch the caller is
    # assigned to needs only REPORTS_BRANCH. A branch manager therefore reads
    # their branch and is refused the consolidated view — which is the whole
    # distinction, and it would be lost under a single "reports" permission.
    #
    # An operational principal with no branch named falls back to their own
    # assignments rather than being refused: `reports` scopes every aggregate
    # through `branch_scope`, so "all branches" already means "all of mine".
    def check(self, request, needed: str | None = None):
        """Returns an error Response, or None when the caller may proceed."""
        branch_id = self._branch(request)

        if needed is None:
            confined = access.branch_scope(request.user)
            needed = (
                access.REPORTS_ORGANISATION
                if branch_id is None and confined is None
                else access.REPORTS_BRANCH
            )

        if not access.may(request.user, needed, branch_id):
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
        start, end = reports.parse_window(request)
        return Response(_exact(
            reports.overview(request.user, start, end, self._branch(request))
        ))

    @action(detail=False, methods=["get"], url_path="by-branch")
    def by_branch(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = reports.parse_window(request)
        return Response(
            _exact({"branches": reports.by_branch(request.user, start, end)})
        )

    @action(detail=False, methods=["get"], url_path="by-product")
    def by_product(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = reports.parse_window(request)
        return Response(_exact(
            {
                "products": reports.by_product(
                    request.user, start, end, self._branch(request)
                )
            }
        ))

    @action(detail=False, methods=["get"], url_path="by-cashier")
    def by_cashier(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = reports.parse_window(request)
        return Response(_exact(
            {
                "cashiers": reports.by_cashier(
                    request.user, start, end, self._branch(request)
                )
            }
        ))

    @action(detail=False, methods=["get"], url_path="by-payment-method")
    def by_payment_method(self, request):
        refused = self.check(request)
        if refused is not None:
            return refused
        start, end = reports.parse_window(request)
        return Response(_exact(
            {
                "methods": reports.by_payment_method(
                    request.user, start, end, self._branch(request)
                )
            }
        ))

    @action(detail=False, methods=["get"], url_path="stock-alerts")
    def stock_alerts(self, request):
        # Not a reporting permission: what is running low is a stockroom
        # question, and a clerk who may adjust stock plainly may see which
        # shelves are empty. A cashier holds INVENTORY_VIEW too — they are
        # the person who notices first.
        refused = self.check(request, access.INVENTORY_VIEW)
        if refused is not None:
            return refused
        return Response(
            _exact({"alerts": reports.stock_alerts(request.user, self._branch(request))})
        )

    @action(detail=False, methods=["get"], url_path="register-status")
    def register_status(self, request):
        # Always the branch permission, even with no branch named: this lists
        # open tills and what should be in their drawers, which is an
        # operational view of a branch rather than a consolidated one — and
        # the aggregate is already confined by `branch_scope`.
        refused = self.check(request, access.REPORTS_BRANCH)
        if refused is not None:
            return refused
        return Response(
            _exact(
                {"registers": reports.register_status(request.user, self._branch(request))}
            )
        )
