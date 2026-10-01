"""
What a product costs, at a branch, on a day — in one place.

═══════════════════════════════════════════════════════════════════════════════
THERE IS ONE ANSWER AND ONE IMPLEMENTATION OF IT.

The till shows a price, the checkout charges a price, the receipt prints one
and a report measures a margin against one. If any two of those work the price
out separately, they will eventually disagree — and the first anybody hears of
it is a customer at the counter being charged something other than the shelf
edge said.

So `sales/services.checkout` does not read `selling_price`, the product
endpoint does not read `selling_price`, and neither of them knows what a price
list is. Both call this module. The same reason `identity/access.py` is the
only place a permission is decided and `portal/selectors.py` is the only place
a tenant is scoped.
═══════════════════════════════════════════════════════════════════════════════

── THE RULE, IN FULL ───────────────────────────────────────────────────────────

A list APPLIES when all of these hold:

    · it belongs to this organisation
    · it is active
    · it names this branch, or it names no branch at all
    · the day is inside its window, or it has no window

Applying lists are walked from the HIGHEST precedence down, and the first one
holding an entry for the product wins. Not the highest-precedence list alone:
a promotion listing three items must not blank out the prices of everything it
does not mention.

Precedence is unique per organisation at the database level, so the walk has a
total order and a tie is not a thing that can be stored. That is the whole
reason the constraint is there.

Nothing applies → `product.selling_price`, which remains the answer in the
overwhelming majority of shops, which have no price lists at all.

── IT IS BULK-FIRST, AND THAT IS NOT PREMATURE ─────────────────────────────────

A till opens with the whole catalogue on screen. Resolving one product at a
time would be two queries per product — four hundred queries to draw a till,
on a connection in a shop. So `prices_for` takes every product at once and
costs two queries whatever the count, and `price_for` is a thin wrapper over
it rather than the other way round.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from .models import PriceList, PriceListEntry


def applicable_lists(organization_id: int, *, branch_id=None, on=None):
    """
    The lists in force for this branch on this day, strongest first.

    ── A LIST WITH NO BRANCHES APPLIES EVERYWHERE ──────────────────────────
    Expressed as "has no branch links", not as "has a null branch", because
    a list can name two branches out of five and a nullable column cannot
    say that.
    """
    on = on or timezone.localdate()

    rows = PriceList.objects.filter(
        organization_id=organization_id, is_active=True
    ).filter(
        Q(starts_on__isnull=True) | Q(starts_on__lte=on),
        Q(ends_on__isnull=True) | Q(ends_on__gte=on),
    )

    if branch_id is None:
        # No branch in hand — the organisation-wide lists are the only ones
        # that can be said to apply. Guessing at a branch's own prices
        # without being told the branch is how a catalogue screen comes to
        # show one shop's promotion to another.
        rows = rows.filter(branch_links__isnull=True)
    else:
        rows = rows.filter(
            Q(branch_links__isnull=True) | Q(branch_links__branch_id=branch_id)
        )

    # distinct(): the branch join can return a list twice when it names the
    # branch more than once — it cannot today, because of the unique
    # constraint, but a queryset that depends on a constraint elsewhere for
    # its row count is one bad migration from double-counting.
    return rows.distinct().order_by("-precedence", "pk")


def prices_for(
    products, *, branch_id=None, on=None
) -> dict[int, tuple[Decimal, PriceList | None]]:
    """
    `{product_id: (price, the list it came from or None)}` for every product
    given.

    The list is returned beside the price because "why was this 80 when the
    shelf says 100" is a question somebody asks weeks later, and the answer
    has to be recorded on the sale rather than reconstructed from a
    configuration that has since changed.
    """
    products = list(products)
    if not products:
        return {}

    resolved = {
        product.pk: (Decimal(product.selling_price), None) for product in products
    }

    # Every product here belongs to one organisation — a sale cannot span two
    # and neither can a catalogue screen. Taking it from the products rather
    # than from an argument means a caller cannot pass the wrong one.
    organisations = {product.organization_id for product in products}
    if len(organisations) != 1:
        raise ValueError(
            "prices_for was given products from more than one organisation; "
            "a price list belongs to exactly one business"
        )
    organization_id = organisations.pop()

    lists = list(applicable_lists(organization_id, branch_id=branch_id, on=on))
    if not lists:
        return resolved

    by_list = {price_list.pk: price_list for price_list in lists}
    # One query for every entry that could matter, rather than one per list.
    entries = PriceListEntry.objects.filter(
        price_list_id__in=by_list, product_id__in=[p.pk for p in products]
    ).values_list("price_list_id", "product_id", "price")

    # Walk strongest first and take the first hit, so a weaker list cannot
    # overwrite a stronger one. `lists` is already ordered.
    seen: set[int] = set()
    found: dict[tuple[int, int], Decimal] = {
        (list_id, product_id): price for list_id, product_id, price in entries
    }
    for price_list in lists:
        for product in products:
            if product.pk in seen:
                continue
            price = found.get((price_list.pk, product.pk))
            if price is None:
                continue
            resolved[product.pk] = (Decimal(price), price_list)
            seen.add(product.pk)

    return resolved


def price_for(product, *, branch_id=None, on=None) -> tuple[Decimal, PriceList | None]:
    """One product. A wrapper over `prices_for`; see the banner on why."""
    return prices_for([product], branch_id=branch_id, on=on)[product.pk]
