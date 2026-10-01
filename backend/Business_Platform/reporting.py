"""
The pieces every report needs, and no report should own.

This module exists because a second domain started reporting. Window parsing,
two-decimal quantisation and the Decimal-to-string walk were all written in
`sales/reports.py`, which was the right place while sales was the only thing
with a dashboard. Procurement reports now too, and it has no relationship with
sales — importing them from there would make buying depend on selling for the
meaning of "this month".

Nothing here knows what is being reported on. Anything that does belongs in the
domain's own `reports.py`.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

CENTS = Decimal("0.01")


def q(value) -> Decimal:
    """
    Two decimal places, always.

    A Sum over a DecimalField comes back with whatever scale the database felt
    like — SQLite hands back `300` where Postgres hands back `300.00`. Rendered
    straight, the same report reads differently on a developer's machine and in
    production, and only one of them looks like money.
    """
    return (Decimal(value or 0)).quantize(CENTS)


def exact(value):
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
        return {key: exact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [exact(item) for item in value]
    return value


# The windows a screen offers, resolved HERE rather than by whoever is asking.
#
# ── WHY THE CLIENT MUST NOT WORK OUT "TODAY" ITSELF ─────────────────────────
#
# It gets it wrong, and silently. A Next server rendering a page computes dates
# in ITS clock, which is UTC in a container; a shop in Nairobi is three hours
# ahead, so for three hours every night the frontend asks for yesterday and a
# dashboard that should read 302.00 reads 0.00. It looks like a day with no
# trade rather than like a bug, which is the worst way for it to look.
#
# So the periods have names, and the one clock that knows what they mean is the
# one the rows were stamped against.
RANGES = ("today", "week", "month", "year")


def _named_window(name: str, today):
    if name == "week":
        return today - timedelta(days=6), today
    if name == "month":
        return today.replace(day=1), today
    if name == "year":
        return today.replace(month=1, day=1), today
    return today, today


def parse_window(request) -> tuple[datetime, datetime]:
    """
    The reporting window, defaulting to today in the shop's timezone.

    Either `range=today|week|month|year`, or explicit `from`/`to` dates.

    Dates are read as whole local days — `from=2026-09-01&to=2026-09-30`
    includes everything that happened on the 30th, not everything up to
    midnight at its start. An off-by-one here silently drops the last day of
    every month-end report, and month-end is when somebody actually reads one.
    """
    now = timezone.localtime()
    raw_range = (request.query_params.get("range") or "").strip().lower()
    raw_from = (request.query_params.get("from") or "").strip()
    raw_to = (request.query_params.get("to") or "").strip()

    if raw_range in RANGES:
        # A named range wins outright. Honouring a stray `from` beside it
        # would give two answers to one question.
        start_date, end_date = _named_window(raw_range, now.date())
        tz = timezone.get_current_timezone()
        return (
            timezone.make_aware(datetime.combine(start_date, time.min), tz),
            timezone.make_aware(datetime.combine(end_date, time.max), tz),
        )

    def as_date(value, fallback):
        if not value:
            return fallback
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return fallback

    start_date = as_date(raw_from, now.date())
    end_date = as_date(raw_to, start_date)

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    return start, end
