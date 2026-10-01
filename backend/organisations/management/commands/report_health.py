"""
Tell Genmars this installation is alive, on a timer.

── WHY IT LIVES IN `organisations` ────────────────────────────────────────────

A management command has to sit in an installed app, and this one is about the
whole deployment rather than any one domain. `organisations` is the app that
already holds what a deployment IS — the tenants on it — so it is the least
wrong home. `Business_Platform` would be the right one and is not in
INSTALLED_APPS.

── AND WHY A TIMER RATHER THAN A THREAD ───────────────────────────────────────

business-os has no task queue and this is not a reason to acquire one. The
backup and the restore drill already run as systemd timers on this host, so a
heartbeat is the same shape as things that already work here — and a job that
fails tells somebody, through the same `genmars-alert@` handler, instead of
dying quietly inside a worker process.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from Business_Platform import parent


class Command(BaseCommand):
    help = "Report health to the Genmars system registry."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Work out the health and print it without sending anything.",
        )

    def handle(self, *args, **options):
        health, detail = parent.assess()
        line = f"{health}: {detail}"

        if options["dry_run"]:
            self.stdout.write(line)
            return

        if parent.heartbeat():
            self.stdout.write(self.style.SUCCESS(f"reported — {line}"))
            return

        # ── NON-ZERO, SO THE TIMER'S OnFailure FIRES ───────────────────────
        #
        # A heartbeat that fails silently is worse than none: the dashboard
        # shows the system as last-seen-hours-ago, which reads as "the
        # application is down" when the truth may be that only the reporting
        # is. Exiting non-zero makes the reporting failure its own alert.
        raise SystemExit(f"could not report to Genmars — {line}")
