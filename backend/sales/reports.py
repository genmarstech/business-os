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

    ══════════════════════════════════════════════════════════════════════════
    THE DRAWER FIGURE IS NOT COMPUTED HERE. IT IS ASKED FOR.

    `branches.services.drawer` already carried a banner saying "ONE
    IMPLEMENTATION, SHARED WITH THE REPORT" — and it was not shared. This
    function held a second copy of `opening + cash taken − change given`, and
    the two stayed in step only because nobody had changed either.

    Then cash movements were added to one of them. A shop that banked its
    takings at lunchtime would have seen the close screen expect one figure
    and this dashboard expect another, several hundred shillings apart, with
    no way to tell which was lying. That is the exact failure the banner
    describes, arrived at the exact way it predicted.

    So the claim is now true: one implementation, called from both.
    ══════════════════════════════════════════════════════════════════════════
    """
    from branches.models import RegisterShift
    from branches.services import drawer

    shifts = scoped(
        RegisterShift.objects.select_related("register", "register__branch", "operator"),
        user,
        "register__branch__organization_id",
    ).filter(status="OPEN")

    shifts = _confine(shifts, user, "register__branch_id", branch_id)

    out = []
    for shift in shifts:
        counts = drawer(shift)

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
                "opening_cash": counts["opening_cash"],
                "cash_taken": counts["cash_taken"],
                "change_given": counts["change_given"],
                # New, and the reason this dashboard can now be trusted beside
                # the close screen: cash that moved mid-shift.
                "paid_in": counts["paid_in"],
                "paid_out": counts["paid_out"],
                # Returns taken at the till. Carried for the same reason as
                # the two above — a manager comparing this screen with the
                # close has to see the same reasons for the same figure.
                "refunded_cash": counts["refunded_cash"],
                "expected_cash": counts["expected_cash"],
                "revenue": q(sold["revenue"]),
                "transactions": sold["transactions"] or 0,
            }
        )
    return out


def drawers_counted(user, start, end, branch_id=None, limit=100) -> dict:
    """
    The drawers that have already been counted, and by how much each was out.

    ══════════════════════════════════════════════════════════════════════════
    THE VARIANCE WAS PRODUCED AND THEN UNREADABLE, WHICH IS MOST OF THE WAY TO
    NOT PRODUCING IT.

    Closing a till computes a variance, puts it in a notification, and stores
    the count it came from. After that there was nowhere to see it. The only
    screen that showed a drawer figure was `register_status`, which filters
    `status="OPEN"` — so a shift's variance was visible for exactly as long as
    the shift was not yet closed, and vanished at the moment it acquired one.

    A shop could therefore be short two hundred shillings every Friday for a
    year and nobody could find that out, because the question "which drawers
    did not balance" had no answer anywhere. A count nobody can look back at
    is a count that only disciplines the person who happened to be watching.
    ══════════════════════════════════════════════════════════════════════════

    ── WHY EACH SHIFT IS ASKED SEPARATELY, AND WHY THAT IS NOT A MISTAKE ─────

    This is a query per shift, which the other functions in this module go out
    of their way to avoid. It is deliberate: `branches.services.drawer` is the
    ONE implementation of what a till should be holding, and its banner says
    so. Rewriting that arithmetic as an annotation over the shift table — six
    terms across three tables, two of them directional — would make this the
    third copy, and the two existing copies have already disagreed once, with
    the close screen and the dashboard several hundred shillings apart.

    So the cost is paid where it is cheapest to pay: `limit` bounds it. A
    window with more closed shifts than that is reported as truncated rather
    than silently cut, because "every drawer balanced" and "every drawer we
    looked at balanced" are different statements and only one of them is safe
    to put in front of a manager.

    ── `closed_at`, NOT `opened_at` ──────────────────────────────────────────

    A shift is placed in the window by when it was COUNTED. A night shift that
    opens at 22:00 on Friday and is counted at 06:00 on Saturday belongs to
    the day somebody reconciled it, which is the day the money was handled and
    the day a manager would go looking for it.
    """
    from branches.models import RegisterShift
    from branches.services import drawer

    shifts = scoped(
        RegisterShift.objects.select_related("register", "register__branch", "operator"),
        user,
        "register__branch__organization_id",
    ).filter(
        status="CLOSED",
        closed_at__gte=start,
        closed_at__lte=end,
    )

    shifts = _confine(shifts, user, "register__branch_id", branch_id)

    # Newest first: the drawer counted an hour ago is the one still worth
    # asking somebody about.
    total = shifts.count()
    shifts = shifts.order_by("-closed_at")[:limit]

    rows = []
    short_count = 0
    over_count = 0
    net_total = ZERO
    worst = ZERO

    for shift in shifts:
        counts = drawer(shift)
        variance = counts["variance"]

        # ⚠ NULL IS POSSIBLE HERE AND IS NOT ZERO.
        #
        # `close_shift` always writes a count, so a shift closed through it
        # has a variance. A row closed before that service existed — status
        # moved by the PATCH the service was written to replace — has
        # `closing_cash` of NULL and therefore no variance at all. Counting
        # that as balanced would report a drawer nobody ever counted as one
        # that came out exactly right, which is the most flattering possible
        # lie about it.
        if variance is None:
            uncounted = True
        else:
            uncounted = False
            net_total += variance
            if variance < ZERO:
                short_count += 1
                if variance < worst:
                    worst = variance
            elif variance > ZERO:
                over_count += 1

        rows.append(
            {
                "shift": shift.pk,
                "register": shift.register_id,
                "register_name": shift.register.name,
                "branch": shift.register.branch_id,
                "branch_name": shift.register.branch.branch_name,
                # ⚠ THE OPERATOR, WHICH IS NOT THE PERSON WHO COUNTED.
                #
                # Nothing records who closed a shift — `close_shift` writes
                # the count and the time and no actor. This is who was ON the
                # till, and a screen that labels it "counted by" would be
                # naming the one person the permission model deliberately
                # keeps out of the count.
                "operator": shift.operator_id,
                "operator_name": shift.operator.full_name,
                "opened_at": shift.opened_at,
                "closed_at": shift.closed_at,
                "opening_cash": counts["opening_cash"],
                "cash_taken": counts["cash_taken"],
                "change_given": counts["change_given"],
                "paid_in": counts["paid_in"],
                "paid_out": counts["paid_out"],
                "refunded_cash": counts["refunded_cash"],
                "expected_cash": counts["expected_cash"],
                "counted_cash": counts["counted_cash"],
                "variance": variance,
                "uncounted": uncounted,
                "note": shift.note,
            }
        )

    counted = [row for row in rows if not row["uncounted"]]

    return {
        "shifts": rows,
        "summary": {
            "closed": len(rows),
            "counted": len(counted),
            # Balanced is derived by subtraction rather than counted in the
            # loop, so the four numbers always add up to `counted` — a
            # summary whose parts do not sum to its whole is the fastest way
            # to lose a manager's trust in the rest of the screen.
            "balanced": len(counted) - short_count - over_count,
            "short": short_count,
            "over": over_count,
            # Positive is over, negative is short, and it is a NET: a till
            # 500 short and another 500 over sum to zero, which is why
            # `short` and `worst_short` are carried beside it. Two tills that
            # cancel out is not a shop that balanced.
            "net_variance": q(net_total),
            "worst_short": q(worst),
            # True when the window holds more closed shifts than were read.
            # See the docstring — the summary describes `shifts`, not the
            # window, and saying which is the difference between a figure and
            # a guess.
            "truncated": total > len(rows),
            "closed_in_window": total,
        },
    }
