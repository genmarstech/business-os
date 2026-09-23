#!/usr/bin/env bash
#
# Prove the newest backup can actually be restored.
#
#   ./scripts/restore-test.sh                   # newest dump in ./backups
#   ./scripts/restore-test.sh backups/x.dump    # a specific one
#   ./scripts/restore-test.sh /path/to/x.dump.gpg   # on the laptop with the key
#
# ── WHY THIS SCRIPT EXISTS ──────────────────────────────────────────────────
# Charter 03 §IV Tier 1 requires "automated backup with a TESTED restore". The
# tested half is the half that gets skipped, and skipping it is how a team
# discovers — during the incident — that the dumps have been zero bytes for
# months, or were taken against the wrong database, or restore into a schema
# the current code cannot read.
#
# So this does not merely run pg_restore and check the exit code. It restores
# into a throwaway database and then ASSERTS THE DATA IS THERE, in three steps
# that each catch a different lie:
#
#   1. every table the application cannot run without is present — catches a
#      dump taken against a stale or wrong schema;
#   2. organisations and migration history are non-empty — catches the
#      schema-only restore that reports success into an empty database;
#   3. for sales, payments and refunds, the restored row counts match what was
#      live when the dump began — catches a PARTIAL restore, which the first
#      two steps would happily wave through.
#
# ⚠ STEP 3 MATTERS MORE HERE THAN IT DOES IN gen-portal. These rows are other
#   businesses' takings. A restore that brings back nine sales out of twelve
#   passes every "is it empty" check and quietly loses somebody's money.
#
# It is safe to run against production. The scratch database is created and
# dropped by this script and is never the one the application uses; the live
# database is only ever read from, never written to.

set -euo pipefail

cd "$(dirname "$0")/.."

DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_USER="${POSTGRES_USER:-business_platform}"
POSTGRES_DB="${POSTGRES_DB:-business_platform}"
SCRATCH_DB="${SCRATCH_DB:-business_restore_check}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

dump_path="${1:-}"
if [ -z "$dump_path" ]; then
    dump_path="$(find "$BACKUP_DIR" -maxdepth 1 \
        \( -name 'business-*.dump' -o -name 'business-*.dump.gpg' \) \
        -type f | sort | tail -1)"
fi

if [ -z "$dump_path" ] || [ ! -s "$dump_path" ]; then
    echo "FATAL: no non-empty dump found. Nothing to test." >&2
    exit 1
fi

dump_name="$(basename "$dump_path")"
echo "==> Testing restore of ${dump_name}"

# ── DECRYPTING, WHEN THERE IS SOMETHING TO DECRYPT ──────────────────────────
#
# The archive on the server is plaintext, so a scheduled run never reaches this
# branch — the server holds only the public half of the key and could not
# decrypt anyway. See the long note in backup.sh for why it is arranged so.
#
# This exists for the drill that matters: running this on the machine that
# HOLDS the private key, against a collected .gpg file. That is the only thing
# proving an off-box backup can actually be opened, and it is a failure with no
# symptoms — gpg encrypts happily to a key whose private half was lost months
# ago, and every copy since is unreadable with nothing saying so.
decrypted=""
cleanup_plaintext() {
    if [ -n "$decrypted" ]; then
        rm -f "$decrypted"
        docker compose exec -T "$DB_SERVICE" rm -f "/backups/$(basename "$decrypted")" \
            >/dev/null 2>&1 || true
    fi
}
trap cleanup_plaintext EXIT

case "$dump_name" in
*.gpg)
    echo "==> Decrypting (this is the half of the test that proves the key works)"
    decrypted="${BACKUP_DIR}/.restore-check-$$.dump"
    # NO --batch. The private key is passphrase-protected, and --batch tells gpg
    # never to prompt — which fails with "No passphrase given" and a message
    # that reads like the key is lost, when nobody has been asked for it.
    if ! gpg --yes --quiet --output "$decrypted" --decrypt "$dump_path"; then
        echo "FATAL: could not decrypt ${dump_name}." >&2
        echo >&2
        # Ordered by likelihood, not by drama. The first two are ordinary and
        # fixable in a minute; only the third is the emergency, and announcing
        # an emergency for a mistyped passphrase is how a real one gets
        # disbelieved later.
        echo "  1. Wrong or unentered passphrase — try again." >&2
        echo "  2. This machine does not hold the private key. Check with:" >&2
        echo "       gpg --list-secret-keys ${BACKUP_RECIPIENT:-<key id>}" >&2
        echo "  3. If the key is genuinely gone, THAT is the emergency: every" >&2
        echo "     backup since encryption was switched on is unreadable." >&2
        exit 1
    fi
    chmod 600 "$decrypted"
    dump_name="$(basename "$decrypted")"
    ;;
esac

psql_scratch() {
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$SCRATCH_DB" -tAc "$1"
}

# Read-only, and only ever used for counting. The live database is never
# written to by this script.
psql_live() {
    docker compose exec -T "$DB_SERVICE" \
        psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"
}

cleanup() {
    echo "==> Dropping scratch database ${SCRATCH_DB}"
    docker compose exec -T "$DB_SERVICE" \
        dropdb -U "$POSTGRES_USER" --if-exists --force "$SCRATCH_DB" >/dev/null 2>&1 || true
}
# Runs on success, failure and Ctrl-C alike. Without it a failed run leaves a
# scratch database behind and the next run fails for the wrong reason.
#
# Chained with cleanup_plaintext, set earlier: bash keeps only ONE EXIT trap, so
# replacing it here would leave a decrypted copy of the whole database sitting
# in the backups directory after every run.
trap 'cleanup; cleanup_plaintext' EXIT

cleanup
echo "==> Creating scratch database ${SCRATCH_DB}"
docker compose exec -T "$DB_SERVICE" createdb -U "$POSTGRES_USER" "$SCRATCH_DB"

echo "==> Restoring"
# --exit-on-error matters. Without it pg_restore reports individual failures and
# still exits 0, so a restore missing half its tables looks like a success.
docker compose exec -T "$DB_SERVICE" \
    pg_restore -U "$POSTGRES_USER" -d "$SCRATCH_DB" --no-owner --exit-on-error \
    "/backups/${dump_name}"

# ── the assertions ──────────────────────────────────────────────────────────

echo "==> Checking the restored schema"

failures=0

require_table() {
    local table="$1"
    local present
    present="$(psql_scratch "SELECT to_regclass('public.${table}') IS NOT NULL;")"
    if [ "$present" = "t" ]; then
        echo "    ok       ${table}"
    else
        echo "    MISSING  ${table}" >&2
        failures=$((failures + 1))
    fi
}

# Every table the application cannot function without. Losing any one means the
# restore is not usable, whatever pg_restore reported.
#
# The tenancy tables come first deliberately: without them the rest is a pile
# of rows belonging to nobody, which is worse than no restore because it looks
# like one.
require_table organisations_businessorganization
require_table organisations_organizationstaff
require_table identity_platformaccount
require_table identity_tenantmembership
require_table identity_staffcredential
require_table branches_branches
require_table branches_register
require_table branches_registershift
require_table catalog_catalogcategoryproduct
require_table catalog_taxrule
require_table inventory_stocklevel
require_table inventory_stockmovement
require_table sales_sale
require_table sales_saleitem
require_table sales_payment
require_table sales_refund
require_table sales_receipt
require_table django_migrations

echo "==> Checking the restored data"

orgs="$(psql_scratch 'SELECT count(*) FROM organisations_businessorganization;')"
migrations="$(psql_scratch 'SELECT count(*) FROM django_migrations;')"

echo "    organisations rows:     ${orgs}"
echo "    django_migrations rows: ${migrations}"

# No organisations means the dump captured a schema and no content — precisely
# the "successful" restore that is worthless in an incident.
if [ "${orgs:-0}" -lt 1 ]; then
    echo "    FAIL: no organisations restored. A schema-only restore is not a backup." >&2
    failures=$((failures + 1))
fi

if [ "${migrations:-0}" -lt 1 ]; then
    echo "    FAIL: no migration history. The schema will not match the code." >&2
    failures=$((failures + 1))
fi

# ── content completeness ────────────────────────────────────────────────────
# The checks above prove the restore is not empty. They do not prove it is
# COMPLETE, and for tables carrying money "not empty" is a long way from good
# enough: a dump that captured three of nine sales sails through everything
# above.
#
# So compare counts. pg_dump takes its snapshot when it starts and the filename
# carries that moment in UTC, so every row created before it must be present.
# Rows created afterwards are legitimately absent and are excluded from the
# live count rather than treated as loss.
#
# Restored > expected is not a failure: it means rows were deleted from live
# after the dump, which is the backup doing its job. Still printed, because on
# a sales ledger that is worth a human's attention.

# business-20260923-192552.dump -> 2026-09-23 19:25:52+00
# Read from the ORIGINAL filename, not the decrypted temporary one, which
# carries a process id instead of a timestamp.
stamp="$(basename "$dump_path" | sed -nE 's/^business-([0-9]{4})([0-9]{2})([0-9]{2})-([0-9]{2})([0-9]{2})([0-9]{2})\.dump(\.gpg)?$/\1-\2-\3 \4:\5:\6+00/p')"

if [ -z "$stamp" ]; then
    echo "    note: ${dump_name} is not a scheduled dump name; skipping the"
    echo "          count comparison, which needs the dump's timestamp."
else
    echo "==> Comparing row counts against the live database (as at ${stamp})"

    compare_counts() {
        local table="$1"
        local expected restored

        # A table missing from the restore is already recorded by
        # require_table. Counting it here would abort on a raw psql error and
        # hide the remaining comparisons, so skip and let the clearer failure
        # stand.
        if [ "$(psql_scratch "SELECT to_regclass('public.${table}') IS NOT NULL;")" != "t" ]; then
            echo "    skipped  ${table}: not in this dump (see MISSING above)"
            return
        fi

        expected="$(psql_live "SELECT count(*) FROM ${table} WHERE created_at < '${stamp}';")"
        restored="$(psql_scratch "SELECT count(*) FROM ${table};")"

        if [ "${restored:-0}" -lt "${expected:-0}" ]; then
            echo "    LOST     ${table}: ${restored} restored, ${expected} expected" >&2
            failures=$((failures + 1))
        elif [ "${restored:-0}" -gt "${expected:-0}" ]; then
            echo "    ok       ${table}: ${restored} restored (${expected} live now — rows deleted since)"
        else
            echo "    ok       ${table}: ${restored}"
        fi
    }

    # Only tables with a created_at, and only where losing a row is a business
    # problem rather than an inconvenience.
    #
    # ⚠ sales_saleitem, sales_receipt and inventory_stocklevel are NOT here,
    #   and not because they do not matter — they have no created_at column, so
    #   there is no way to say which of their rows predate the dump. They are
    #   covered by require_table only. A sale whose ITEMS did not restore is a
    #   gap this test cannot currently see; closing it means giving those
    #   tables a timestamp, which is a migration and its own change.
    compare_counts organisations_businessorganization
    compare_counts organisations_organizationstaff
    compare_counts sales_sale
    compare_counts sales_payment
    compare_counts sales_refund
    compare_counts inventory_stockmovement
    compare_counts catalog_catalogcategoryproduct
fi

echo
if [ "$failures" -ne 0 ]; then
    echo "RESTORE TEST FAILED — ${failures} problem(s) with ${dump_name}." >&2
    echo "Treat the backups as unusable until this passes." >&2
    exit 1
fi

echo "RESTORE TEST PASSED — ${dump_name} restores and contains data."
echo "Tier 1 asks for a tested restore, and 'tested' means recently, not once."
