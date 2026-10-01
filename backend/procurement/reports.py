"""
What the shop is buying, from whom, and what it has not received yet.

═══════════════════════════════════════════════════════════════════════════════
EVERY FIGURE HERE IS COMPUTED OVER A SCOPED QUERYSET.

The same rule as `sales/reports.py`, for the same reason: a report is the
easiest place in a multi-tenant system to leak everything at once. Nothing in
this module accepts a queryset — each function takes the caller and builds its
own through `identity.permissions.scoped`.
═══════════════════════════════════════════════════════════════════════════════

── ORDERING IS A COMMITMENT; RECEIVING IS A COST ───────────────────────────────

These are different facts and this module never adds them together.

"What has this supplier cost us this quarter" is answered by what actually
ARRIVED — a goods receipt is the moment a liability becomes real. An order is a
promise, and promises get cancelled, part-filled and revised. Reporting ordered
value as spend overstates every quarter in which anything is still in transit,
and overstates it permanently wherever an order was cancelled.

Both figures appear, side by side and labelled, in the same way `sales/reports`
reports refunds beside revenue instead of netting them off. Two true numbers
beat one number that hides which question it answered.

── A DRAFT IS NOT A COMMITMENT ─────────────────────────────────────────────────

`COMMITTED` deliberately starts at SUBMITTED. A draft exists only inside the
shop; nobody has been asked for anything and nothing is owed. Counting drafts
as money committed would let an abandoned shopping list sit in the obligations
figure for ever.

── "OUTSTANDING" TAKES NO WINDOW, AND THAT IS NOT AN OVERSIGHT ─────────────────

Everything else here is an aggregate over a period. `outstanding` is a
POSITION: what is owed to the shop right now. "What was outstanding during
September" is not a question with one answer, and a date filter on it would
quietly produce a number that looks like one.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Sum
from django.utils import timezone

from Business_Platform.reporting import q
from identity.access import scoped_to_branch
from identity.permissions import scoped

from .models import GoodsReceipt, GoodsReceiptItem, PurchaseOrder

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=14, decimal_places=2)

# What the shop has actually asked a supplier for and not finished receiving.
# See the banner: this starts at SUBMITTED, not DRAFT.
COMMITTED = (
    PurchaseOrder.Status.SUBMITTED,
    PurchaseOrder.Status.APPROVED,
    PurchaseOrder.Status.PART_RECEIVED,
)


# ── FUNCTIONS, NOT CONSTANTS — the same trap as in sales/reports.py ─────────
#
# A module-level ExpressionWrapper looks like a tidy constant and is a shared
# mutable object: Django resolves an expression against the query it is used
# in, and the second query to reuse the instance fails with "is an aggregate",
# a message that points nowhere near the cause.
def received_value():
    """What a delivery line was worth: what arrived, at what it cost."""
    return ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=MONEY)


def outstanding_value():
    """What an order line still owes, at the price the order was placed at."""
    return ExpressionWrapper(
        (F("items__quantity_ordered") - F("items__quantity_received"))
        * F("items__unit_cost"),
        output_field=MONEY,
    )


# ── BOTH SCOPES, ALWAYS, IN THAT ORDER ──────────────────────────────────────
#
# `scoped` answers "which organisation", `scoped_to_branch` answers "which
# branch". Omitting the second is how `sales/reports.py` came to show a branch
# manager the whole organisation's takings, and the same mistake is available
# here: `PurchaseOrderViewSet` sets `branch_path`, so the LIST a branch manager
# reads stops at their branch while an unconfined aggregate over the same rows
# would not. A report must not be a way around the scoping of the screen it
# summarises.
#
# `scoped_to_branch` returns the queryset untouched when `branch_scope` is
# None, so a subscriber is unaffected — None means organisation-wide authority,
# not "no branches".
def _confine(rows, user, branch_path, branch_id):
    rows = scoped_to_branch(rows, user, branch_path)
    if branch_id:
        # Narrows further, never widens: naming somebody else's branch matches
        # nothing rather than reaching it.
        rows = rows.filter(**{branch_path: branch_id})
    return rows


def _orders(user, start, end, branch_id=None):
    """Orders RAISED in the window, within the caller's tenants."""
    rows = scoped(PurchaseOrder.objects.all(), user, "organization_id").filter(
        created_at__gte=start, created_at__lte=end
    )
    return _confine(rows, user, "branch_id", branch_id)


def _receipts(user, start, end, branch_id=None):
    """
    Deliveries BOOKED IN during the window.

    Dated by `received_at` rather than `created_at`, because that is the field
    the receiving clerk controls — a delivery entered on Monday morning for
    goods that arrived Friday night belongs in Friday's figures.
    """
    rows = scoped(GoodsReceipt.objects.all(), user, "organization_id").filter(
        received_at__gte=start, received_at__lte=end
    )
    return _confine(rows, user, "branch_id", branch_id)


def overview(user, start, end, branch_id=None) -> dict:
    """The KPI strip for the buying dashboard."""
    orders = _orders(user, start, end, branch_id)
    receipts = _receipts(user, start, end, branch_id)

    raised = orders.exclude(status=PurchaseOrder.Status.CANCELLED).aggregate(
        count=Count("id"), value=Sum("total")
    )
    cancelled = orders.filter(status=PurchaseOrder.Status.CANCELLED).aggregate(
        count=Count("id"), value=Sum("total")
    )

    # Aggregated over the items of the SCOPED receipts, never over
    # GoodsReceiptItem directly: the item has no organisation of its own, and
    # reaching it through the scoped receipt is the whole of what keeps this
    # inside the tenant.
    delivered = GoodsReceiptItem.objects.filter(receipt__in=receipts).aggregate(
        value=Sum(received_value()), lines=Count("id")
    )

    return {
        "from": start.date().isoformat(),
        "to": end.date().isoformat(),
        "orders_raised": raised["count"] or 0,
        "ordered_value": q(raised["value"]),
        "orders_cancelled": cancelled["count"] or 0,
        "cancelled_value": q(cancelled["value"]),
        "deliveries": receipts.count(),
        # The spend figure. See the banner on why this and not ordered_value.
        "received_value": q(delivered["value"]),
        "lines_received": delivered["lines"] or 0,
        "suppliers_used": (
            receipts.values("purchase_order__supplier_id").distinct().count()
        ),
    }


def by_supplier(user, start, end, branch_id=None) -> list[dict]:
    """
    What each supplier cost, and what was ordered from them.

    Ordered on spend, because the question this answers is "who are we giving
    our money to" — and the answer is often not the supplier somebody would
    have named.
    """
    spend = {
        row["receipt__purchase_order__supplier_id"]: row
        for row in GoodsReceiptItem.objects.filter(
            receipt__in=_receipts(user, start, end, branch_id)
        )
        .values(
            "receipt__purchase_order__supplier_id",
            "receipt__purchase_order__supplier__name",
        )
        .annotate(value=Sum(received_value()))
    }

    ordered = {
        row["supplier_id"]: row
        for row in _orders(user, start, end, branch_id)
        .exclude(status=PurchaseOrder.Status.CANCELLED)
        .values("supplier_id", "supplier__name")
        .annotate(value=Sum("total"), orders=Count("id"))
    }

    out = []
    for supplier_id in set(spend) | set(ordered):
        paid = spend.get(supplier_id)
        placed = ordered.get(supplier_id)
        out.append(
            {
                "supplier": supplier_id,
                "supplier_name": (
                    paid["receipt__purchase_order__supplier__name"]
                    if paid
                    else placed["supplier__name"]
                ),
                "received_value": q(paid["value"]) if paid else ZERO,
                "orders": placed["orders"] if placed else 0,
                "ordered_value": q(placed["value"]) if placed else ZERO,
            }
        )
    return sorted(out, key=lambda row: row["received_value"], reverse=True)


def by_product(user, start, end, branch_id=None, limit=20) -> list[dict]:
    """What the shop actually buys, by what it spends on it."""
    rows = (
        GoodsReceiptItem.objects.filter(
            receipt__in=_receipts(user, start, end, branch_id)
        )
        .values(
            "order_item__product_id",
            "order_item__product_name",
            "order_item__product_sku",
        )
        # The alias must not shadow the column: `quantity=Sum("quantity")`
        # makes the F("quantity") inside received_value() resolve to the SUM,
        # and Django refuses with "is an aggregate" — an error that names the
        # expression and says nothing about the alias that broke it.
        .annotate(quantity_received=Sum("quantity"), value=Sum(received_value()))
        .order_by("-value")[:limit]
    )
    return [
        {
            "product": row["order_item__product_id"],
            "product_name": row["order_item__product_name"],
            "sku": row["order_item__product_sku"],
            "quantity": q(row["quantity_received"]),
            "value": q(row["value"]),
        }
        for row in rows
    ]


def outstanding(user, branch_id=None) -> dict:
    """
    What has been ordered and not yet delivered, as of now.

    Takes no window — see the banner. Valued at the price on the order, which
    is the price the shop will be invoiced, not whatever the product costs
    today.
    """
    today = timezone.localdate()

    rows = (
        _confine(
            scoped(PurchaseOrder.objects.all(), user, "organization_id"),
            user,
            "branch_id",
            branch_id,
        )
        .filter(status__in=COMMITTED)
        .select_related("supplier", "branch")
        # One Sum over one join. A second aggregate over a different relation
        # in the same query multiplies the rows against each other and both
        # figures come out wrong, in a way that only shows up once an order
        # has more than one of something.
        .annotate(owed=Sum(outstanding_value()))
        .order_by("expected_at", "number")
    )

    orders = []
    total = ZERO
    overdue_total = ZERO
    for order in rows:
        owed = q(order.owed)
        # An expected date is optional, so "overdue" has three states and not
        # two. An order with no date is not late; it is unscheduled, and
        # calling it either would be a guess.
        overdue = order.expected_at is not None and order.expected_at < today
        total += owed
        if overdue:
            overdue_total += owed
        orders.append(
            {
                "order": order.pk,
                "number": order.number,
                "status": order.status,
                "status_label": order.get_status_display(),
                "supplier": order.supplier_id,
                "supplier_name": order.supplier.name,
                "branch": order.branch_id,
                "branch_name": order.branch.branch_name,
                "expected_at": (
                    order.expected_at.isoformat() if order.expected_at else None
                ),
                "overdue": overdue,
                "days_late": (today - order.expected_at).days if overdue else 0,
                "owed": owed,
            }
        )

    return {
        "as_of": today.isoformat(),
        "committed": q(total),
        "overdue": q(overdue_total),
        "orders": orders,
    }


def supplier_reliability(user, start, end, branch_id=None) -> list[dict]:
    """
    Who delivers when they said they would.

    Measured over orders FULLY RECEIVED in the window, against the date the
    shop expected them. An order still in transit is not yet late or early; it
    has no outcome to count, and including it would make every supplier look
    better the slower they are.

    Orders with no expected date cannot be judged at all. They are counted and
    reported rather than quietly dropped, so a reliability figure computed
    from three of a supplier's forty orders says so on its face.
    """
    rows = (
        _confine(
            scoped(PurchaseOrder.objects.all(), user, "organization_id"),
            user,
            "branch_id",
            branch_id,
        )
        .filter(status=PurchaseOrder.Status.RECEIVED)
        .select_related("supplier")
        .annotate(completed=Max("receipts__received_at"))
        .filter(completed__gte=start, completed__lte=end)
    )

    tally: dict[int, dict] = {}
    for order in rows:
        entry = tally.setdefault(
            order.supplier_id,
            {
                "supplier": order.supplier_id,
                "supplier_name": order.supplier.name,
                "orders": 0,
                "on_time": 0,
                "late": 0,
                "unscheduled": 0,
                "_days_late": 0,
            },
        )
        entry["orders"] += 1

        if order.expected_at is None:
            entry["unscheduled"] += 1
            continue

        # Compared as LOCAL dates. `completed` is an aware datetime, and a
        # delivery booked in at 9pm Nairobi is already tomorrow in UTC — which
        # would mark an on-time supplier late, every evening, for ever.
        arrived = timezone.localtime(order.completed).date()
        if arrived <= order.expected_at:
            entry["on_time"] += 1
        else:
            entry["late"] += 1
            entry["_days_late"] += (arrived - order.expected_at).days

    out = []
    for entry in tally.values():
        judged = entry["on_time"] + entry["late"]
        late = entry.pop("_days_late")
        out.append(
            {
                **entry,
                "judged": judged,
                # Over the orders that COULD be judged, not over all of them:
                # an unscheduled order is missing information, not a failure.
                "on_time_rate": (
                    round(entry["on_time"] * 100 / judged) if judged else None
                ),
                # Averaged over the LATE ones only. Averaging the zeros of
                # on-time orders into it produces a small number for a
                # supplier who is occasionally catastrophic, which is the
                # opposite of what the reader needs to know.
                "average_days_late": (
                    round(late / entry["late"], 1) if entry["late"] else None
                ),
            }
        )
    return sorted(out, key=lambda row: (row["late"], row["orders"]), reverse=True)
