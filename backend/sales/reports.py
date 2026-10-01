"""
What the business did — blueprint modules 11 and 12, and §4/§5's dashboards.

═══════════════════════════════════════════════════════════════════════════════
EVERY FIGURE HERE IS COMPUTED OVER A SCOPED QUERYSET.

A report is the easiest place in a multi-tenant system to leak everything at
once: one aggregate over an unscoped table and a shop is reading the platform's
turnover. So nothing in this module takes a queryset — each function takes the
caller and builds its own through `identity.permissions.scoped`, which is the
one place tenant scope is resolved.
═══════════════════════════════════════════════════════════════════════════════

── WHAT COUNTS AS REVENUE, AND WHAT DOES NOT ───────────────────────────────────

Only COMPLETED sales. A held cart is not money and a voided sale never was, and
including either inflates the day's takings against a drawer that will not
match. Refunds are reported alongside rather than silently netted off, because
"we sold 400,000 and gave back 90,000" and "we sold 310,000" are different
facts about a business, and the second hides a returns problem.

── PROFIT IS COMPUTED FROM THE SNAPSHOT, NOT FROM THE CATALOGUE ────────────────

`SaleItem.unit_cost` is what the stock cost at the moment it sold. Joining to
the product's cost price today would restate last quarter's margin every time
a supplier changes their price — which is exactly the number somebody is trying
to trust when they ask what the business earned.

Tax is taken out before margin. VAT collected is not income; it is money held
on behalf of the revenue authority, and a "profit" figure that includes it is
wrong in the direction that gets a business into trouble.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Sum

# Window parsing and money quantisation moved to Business_Platform/reporting.py
# when procurement started reporting too — see the docstring there. They are
# re-imported rather than left behind, so there is one definition of what
# "this month" means across the whole application.
from Business_Platform.reporting import parse_window, q  # noqa: F401
from identity.access import scoped_to_branch
from identity.permissions import scoped
from inventory.models import BranchInventory

from .models import Payment, Refund, Sale, SaleItem

ZERO = Decimal("0.00")

MONEY = DecimalField(max_digits=14, decimal_places=2)


# ── THESE ARE FUNCTIONS, NOT CONSTANTS, AND THAT IS NOT A STYLE CHOICE ──────
#
# A module-level ExpressionWrapper looks like a tidy constant and is a shared
# mutable object: Django resolves an expression against the query it is used
# in, and the second query to reuse the instance fails with "is an aggregate"
# — a message that points nowhere near the actual cause. A fresh instance per
# call costs nothing and cannot be poisoned by an earlier caller.
def net():
    """Line revenue with the tax taken out. See the docstring on why."""
    return ExpressionWrapper(F("line_total") - F("tax_amount"), output_field=MONEY)


def cost():
    return ExpressionWrapper(F("unit_cost") * F("quantity"), output_field=MONEY)


def margin():
    return ExpressionWrapper(
        F("line_total") - F("tax_amount") - (F("unit_cost") * F("quantity")),
        output_field=MONEY,
    )


# ── BOTH SCOPES, ALWAYS, IN THAT ORDER ──────────────────────────────────────
#
# `scoped` answers "which organisation" and `scoped_to_branch` answers "which
# branch", and a report needs both.
#
# For a long time it applied only the first. A branch manager asking for the
# overview with no branch named got the WHOLE organisation's takings, and the
# branch comparison handed them a table of every other branch's revenue — the
# consolidated view §5 reserves for the organisation's own dashboard.
#
# What hid it is that the permission half was right all along: `ReportViewSet`
# correctly demanded REPORTS_BRANCH rather than REPORTS_ORGANISATION, and its
# comment said the aggregate was "already confined by branch_scope". It was
# not; nothing here called it. Choosing the narrower permission and then
# answering the wider question is worse than either mistake alone, because the
# code reads as though it were handled.
#
# `scoped_to_branch` returns the queryset untouched when `branch_scope` is
# None, so a subscriber is unaffected — None means organisation-wide authority,
# not "no branches".
def _confine(rows, user, branch_path, branch_id):
    rows = scoped_to_branch(rows, user, branch_path)
    if branch_id:
        # An explicit branch narrows further and is never widening: it is
        # applied on top of the confinement, so naming somebody else's branch
        # matches nothing rather than reaching it.
        rows = rows.filter(**{branch_path: branch_id})
    return rows


def _sales(user, start, end, branch_id=None):
    """Completed sales in the window, within the caller's tenants."""
    rows = scoped(Sale.objects.all(), user, "organization_id").filter(
        status=Sale.Status.COMPLETED,
        completed_at__gte=start,
        completed_at__lte=end,
    )
    return _confine(rows, user, "branch_id", branch_id)


def _refunds(user, start, end, branch_id=None):
    rows = scoped(Refund.objects.all(), user, "organization_id").filter(
        status=Refund.Status.COMPLETED,
        created_at__gte=start,
        created_at__lte=end,
    )
    return _confine(rows, user, "branch_id", branch_id)


def overview(user, start, end, branch_id=None) -> dict:
    """
    The KPI strip — §4 "Overview / KPIs", §5 "Today's sales".
    """
    sales = _sales(user, start, end, branch_id)

    totals = sales.aggregate(
        revenue=Sum("total"),
        tax=Sum("tax_total"),
        discounts=Sum("discount_total"),
        transactions=Count("id"),
    )
    # Aggregated over the items of the SCOPED sales, never over SaleItem
    # directly: SaleItem has no organisation of its own, and reaching it
    # through the scoped sale is the whole of what keeps this inside the
    # tenant.
    item_totals = SaleItem.objects.filter(sale__in=sales).aggregate(
        items_sold=Sum("quantity"),
        net=Sum(net()),
        cost=Sum(cost()),
        gross_profit=Sum(margin()),
    )

    refunds = _refunds(user, start, end, branch_id).aggregate(
        refunded=Sum("total"), refund_count=Count("id")
    )

    revenue = totals["revenue"] or ZERO
    transactions = totals["transactions"] or 0

    return {
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "revenue": q(revenue),
        "tax_collected": q(totals["tax"]),
        "discounts_given": q(totals["discounts"]),
        "transactions": transactions,
        # Guarded: an empty day is a real answer, not a division error.
        "average_basket": (
            q(revenue / transactions)
            if transactions
            else ZERO
        ),
        "items_sold": q(item_totals["items_sold"]),
        "cost_of_sales": q(item_totals["cost"]),
        "gross_profit": q(item_totals["gross_profit"]),
        # Reported beside revenue, never netted off it. See the docstring.
        "refunded": q(refunds["refunded"]),
        "refunds": refunds["refund_count"] or 0,
    }


def by_branch(user, start, end) -> list[dict]:
    """§4 "Branch comparison"."""
    rows = (
        _sales(user, start, end)
        .values("branch_id", "branch__branch_name")
        .annotate(revenue=Sum("total"), transactions=Count("id"))
        .order_by("-revenue")
    )
    return [
        {
            "branch": row["branch_id"],
            "branch_name": row["branch__branch_name"],
            "revenue": q(row["revenue"]),
            "transactions": row["transactions"],
        }
        for row in rows
    ]


def by_product(user, start, end, branch_id=None, limit=20) -> list[dict]:
    """What actually sells — §12 "product reports"."""
    rows = (
        SaleItem.objects.filter(sale__in=_sales(user, start, end, branch_id))
        .values("product_id", "product_name", "sku")
        # ── THE ALIAS MUST NOT SHADOW THE COLUMN ────────────────────────
        # `quantity=Sum("quantity")` looks harmless and is not: the alias
        # wins, so the F("quantity") inside margin() resolves to the SUM
        # rather than to the column, and Django refuses with "is an
        # aggregate" — an error that names the expression and says nothing
        # about the alias that broke it.
        .annotate(
            quantity_sold=Sum("quantity"),
            revenue=Sum(net()),
            gross_profit=Sum(margin()),
        )
        .order_by("-revenue")[:limit]
    )
    return [
        {
            "product": row["product_id"],
            "product_name": row["product_name"],
            "sku": row["sku"],
            "quantity": q(row["quantity_sold"]),
            "revenue": q(row["revenue"]),
            "gross_profit": q(row["gross_profit"]),
        }
        for row in rows
    ]


def by_cashier(user, start, end, branch_id=None) -> list[dict]:
    """
    §12 "cashier reports". Who took what, which is also how a variance at
    close gets attributed to a shift rather than to the shop in general.
    """
    rows = (
        _sales(user, start, end, branch_id)
        .values("cashier_id", "cashier__full_name")
        .annotate(revenue=Sum("total"), transactions=Count("id"))
        .order_by("-revenue")
    )
    return [
        {
            "cashier": row["cashier_id"],
            "cashier_name": row["cashier__full_name"],
            "revenue": q(row["revenue"]),
            "transactions": row["transactions"],
        }
        for row in rows
    ]


def by_payment_method(user, start, end, branch_id=None) -> list[dict]:
    """
    What the money arrived as — the figure a cash drawer is reconciled
    against, and the one that says how much is sitting in M-Pesa.
    """
    rows = (
        Payment.objects.filter(sale__in=_sales(user, start, end, branch_id))
        .values("method")
        .annotate(amount=Sum("amount"), count=Count("id"))
        .order_by("-amount")
    )
    labels = dict(Payment.Method.choices)
    return [
        {
            "method": row["method"],
            "method_label": labels.get(row["method"], row["method"]),
            "amount": q(row["amount"]),
            "count": row["count"],
        }
        for row in rows
    ]


def stock_alerts(user, branch_id=None, limit=100) -> list[dict]:
    """
    §5 "Stock alerts", module 14 "low-stock alerts".

    At or below the reorder level, not merely below it: a reorder level of ten
    means "reorder when you reach ten", and a strict comparison waits until
    there are nine, which is one sale later than the shop asked for.
    """
    rows = scoped(
        BranchInventory.objects.select_related("product", "branch"),
        user,
        "branch__organization_id",
    ).filter(is_active=True, quantity__lte=F("reorder_level"))

    rows = _confine(rows, user, "branch_id", branch_id)

    return [
        {
            "branch": row.branch_id,
            "branch_name": row.branch.branch_name,
            "product": row.product_id,
            "product_name": row.product.name,
            "quantity": row.quantity,
            "reorder_level": row.reorder_level,
            # Out of stock and low on stock are different urgencies and a
            # dashboard that shows them the same way buries the first.
            "out_of_stock": row.quantity <= 0,
        }
        for row in rows.order_by("quantity")[:limit]
    ]


def register_status(user, branch_id=None) -> list[dict]:
    """
    §5 "Register status" — which tills are open, who is on them, and what has
    gone through since they opened.

    `expected_cash` is opening float plus cash taken minus change given. It is
    what should be in the drawer; comparing it to what is counted is the
    variance in module 7, and it cannot be computed at all without the sales
    this module now has.
    """
    from branches.models import RegisterShift

    shifts = scoped(
        RegisterShift.objects.select_related("register", "register__branch", "operator"),
        user,
        "register__branch__organization_id",
    ).filter(status="OPEN")

    shifts = _confine(shifts, user, "register__branch_id", branch_id)

    out = []
    for shift in shifts:
        taken = Payment.objects.filter(
            sale__shift=shift,
            sale__status=Sale.Status.COMPLETED,
            method=Payment.Method.CASH,
        ).aggregate(cash=Sum("amount"), change=Sum("change_given"))

        cash = taken["cash"] or ZERO
        change = taken["change"] or ZERO
        opening = shift.opening_cash or ZERO

        sold = Sale.objects.filter(
            shift=shift, status=Sale.Status.COMPLETED
        ).aggregate(revenue=Sum("total"), transactions=Count("id"))

        out.append(
            {
                "shift": shift.pk,
                "register": shift.register_id,
                "register_name": shift.register.name,
                "branch": shift.register.branch_id,
                "branch_name": shift.register.branch.branch_name,
                "operator": shift.operator_id,
                "operator_name": shift.operator.full_name,
                "opened_at": shift.opened_at,
                "opening_cash": opening,
                "cash_taken": cash,
                "change_given": change,
                "expected_cash": opening + cash - change,
                "revenue": q(sold["revenue"]),
                "transactions": sold["transactions"] or 0,
            }
        )
    return out
