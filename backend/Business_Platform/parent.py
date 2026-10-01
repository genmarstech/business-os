"""
Telling Genmars this application is alive, and how it is.

══════════════════════════════════════════════════════════════════════════════
THE DIRECTION OF TRUST IS INWARD, AND THIS FILE IS THE INWARD DIRECTION.

CLAUDE.md on the system registry: "a child reports its health and events, the
parent reads them. There is deliberately no 'run command' and no stored
deployment credential — one compromised operations account must not become a
compromise of every system the company touches."

So this POSTs upward and accepts nothing downward. There is no endpoint here
that gen-portal can call, no instruction this module will obey, and the only
credential involved is one business-os holds to identify itself. If this file
ever grows a way for the parent to make something happen here, that rule has
been broken and the registry's whole shape with it.

Until this existed, business-os appeared nowhere in the ops dashboard. It
talked to gen-portal for sign-on and nothing else, so the one application
Genmars sells to other businesses was the one the company could not see
running.
══════════════════════════════════════════════════════════════════════════════

── IT REPORTS A CONSIDERED HEALTH, NOT "THE PROCESS STARTED" ─────────────────

`/healthz` deliberately answers without touching the database — with a single
instance behind Caddy, failing it during a blip converts a 500 into a 502 and
makes a restart loop easy to provoke. That is the right call for a liveness
probe and the wrong one for this.

A heartbeat is the child's own claim about itself, which gen-portal's
HeartbeatView says is the only thing it is authoritative about. So it is worth
making the claim mean something: the database is reachable, the schema matches
the code, and the things that silently stop working are working.

── AND IT IS SPARING WITH "DEGRADED" ─────────────────────────────────────────

gen-portal's own notes are blunt about alerting that becomes background noise:
it "removes the pressure to fix anything". Only two things degrade this
application, and both silently break a promise somebody has already been made:

    unapplied migrations   the code and the schema disagree, and the symptom
                           is a 500 on whichever page touches the new column
    no mail configured     a cashier's password reset and a staff invitation
                           both report success and send nothing

A feature being switched off is not degradation. M-Pesa with no
MPESA_CREDENTIAL_KEY is reported in the detail and nowhere else, because a
shop that has not set it up is not a fault.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

log = logging.getLogger(__name__)

TIMEOUT = 15
USER_AGENT = "business-os (+https://business.genmars.co.ke)"

UP, DEGRADED, DOWN = "up", "degraded", "down"


def assess() -> tuple[str, str]:
    """
    What this application would say about itself, as (health, detail).

    Nothing here raises. A health check that can fail is a health check that
    reports nothing on the day it matters.
    """
    problems: list[str] = []
    notes: list[str] = []

    # ── the database, first, because nothing else means anything without it ──
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception as error:  # noqa: BLE001 — any failure is the same failure
        # The class, not the message: a connection error can carry the DSN,
        # and the DSN carries the password.
        return DOWN, f"database unreachable ({type(error).__name__})"

    # ── the schema the code expects ─────────────────────────────────────────
    try:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            problems.append(f"{len(pending)} migration(s) not applied")
    except Exception as error:  # noqa: BLE001
        problems.append(f"migration state unreadable ({type(error).__name__})")

    # ── the things that fail silently rather than loudly ────────────────────
    if not getattr(settings, "RESEND_API_KEY", ""):
        problems.append("mail not configured")

    # Switched off is not broken. Said, so the dashboard can show it, and not
    # counted against health.
    if not getattr(settings, "MPESA_CREDENTIAL_KEY", ""):
        notes.append("tenant M-Pesa off")
    if not getattr(settings, "ADMIN_REQUIRE_TOTP", False):
        notes.append("admin 2FA optional")

    notes.append(_tenants())

    if problems:
        return DEGRADED, "; ".join(problems + notes)[:300]
    return UP, "; ".join(notes)[:300]


def _tenants() -> str:
    """
    How many shops, and how many are actually trading.

    The second number is the one worth having on a dashboard. A platform with
    fourteen tenants of which three have rung up a sale this week is a
    different business from one where twelve have, and nothing else Genmars
    looks at distinguishes them.
    """
    try:
        from datetime import timedelta

        from django.utils import timezone

        from organisations.models import BusinessOrganization
        from sales.models import Sale

        total = BusinessOrganization.objects.count()
        since = timezone.now() - timedelta(days=7)
        trading = (
            Sale.objects.filter(status=Sale.Status.COMPLETED, completed_at__gte=since)
            .values("organization_id")
            .distinct()
            .count()
        )
        return f"{total} shops, {trading} trading this week"
    except Exception as error:  # noqa: BLE001
        return f"tenant count unavailable ({type(error).__name__})"


def _post(path: str, payload: dict) -> bool:
    key = getattr(settings, "GENMARS_SYSTEM_KEY", "")
    if not key:
        # Absent means off, loudly at the call site and silently here. An
        # installation that is not registered with Genmars is an ordinary
        # state — a developer's laptop, a customer running their own copy.
        return False

    origin = getattr(settings, "GENMARS_API_ORIGIN", "").rstrip("/")
    request = urllib.request.Request(
        f"{origin}{path}",
        data=json.dumps(payload).encode(),
        method="POST",
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Authorization", f"Bearer {key}")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as error:
        # The status and the path. Never the body we sent and never the key —
        # a 401 here means the key is wrong, and logging it to prove that
        # would put it in the log of the application it protects.
        log.error("reporting to Genmars refused: HTTP %s %s", error.code, path)
        return False
    except urllib.error.URLError as error:
        log.error("Genmars unreachable: %s", error.reason)
        return False


def heartbeat() -> bool:
    """"I am running, and here is what I think of myself." """
    health, detail = assess()
    return _post(
        "/api/systems/heartbeat",
        {
            "version": getattr(settings, "APP_VERSION", "") or "unknown",
            "health": health,
            "detail": detail,
        },
    )


def event(message: str, *, level: str = "info", detail: dict | None = None) -> bool:
    """
    "Something happened here." Recorded for a person to read, acted on by
    nobody automatically — which is gen-portal's own description of it.

    ⚠ NOTHING TENANT-IDENTIFYING GOES IN HERE. A SystemEvent is Genmars'
    operational record, not a place to put a customer's name, a cashier's
    details or a figure from somebody's till. Counts and states only.
    """
    return _post(
        "/api/systems/events",
        {"message": message[:300], "level": level, "detail": detail or {}},
    )
