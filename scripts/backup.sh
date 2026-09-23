#!/usr/bin/env bash
#
# Postgres backup for the Business Platform.
#
#   ./scripts/backup.sh
#
# Run from the repository root on the host. Writes a compressed custom-format
# dump to ./backups/ and prunes anything older than the retention window.
#
# ── WHY THIS EXISTS AT ALL ──────────────────────────────────────────────────
# Ported from gen-portal, which has had one since launch. This repository had
# none, and it is the one holding OTHER BUSINESSES' takings — every sale,
# refund and payment a subscriber has taken. Until 2026-09-23 the only backup
# that had ever been made here was typed by hand, minutes before a migration.
#
# ── WHY CUSTOM FORMAT ───────────────────────────────────────────────────────
# `pg_dump -Fc` rather than plain SQL: it is compressed, and pg_restore can
# read it selectively. It also cannot be "restored" by accidentally piping it
# into psql against the live database, which a .sql file invites.
#
# ── THIS SCRIPT IS HALF OF THE JOB ──────────────────────────────────────────
# A backup that has never been restored is not a backup — it is a file. The
# other half is scripts/restore-test.sh, which restores the newest dump into a
# scratch database and checks the data is actually there. Charter 03 §IV Tier 1
# requires the tested restore, not the dump.

set -euo pipefail

cd "$(dirname "$0")/.."

RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_SERVICE="${DB_SERVICE:-db}"
POSTGRES_USER="${POSTGRES_USER:-business_platform}"
POSTGRES_DB="${POSTGRES_DB:-business_platform}"

# ── ENCRYPTION KEY ──────────────────────────────────────────────────────────
#
# A PUBLIC key, and that asymmetry is the entire point. This host can encrypt a
# backup and cannot decrypt one — so somebody who takes the server takes the
# live database (which they already had) and NOT the archive of every earlier
# state of it. A passphrase stored in .env beside the dumps would protect
# against a stolen file and against nothing else.
#
# Unset means unencrypted, loudly. See the warning at the end of this script.
BACKUP_RECIPIENT="${BACKUP_RECIPIENT:-}"
GNUPGHOME="${GNUPGHOME:-/opt/business-os/.gnupg}"
export GNUPGHOME

# Encrypted copies wait here to be collected. Separate from ./backups so a pull
# can take the whole directory without also taking the plaintext archive.
OFFSITE_DIR="${OFFSITE_DIR:-./backups/offsite}"

mkdir -p "$BACKUP_DIR"

# ── PERMISSIONS ─────────────────────────────────────────────────────────────
#
# These files are every subscriber's sales, refunds, stock and staff — other
# people's businesses, not ours. Default mode on a shared host means any
# process running as any user can read all of it.
#
# Set on every run rather than once by hand: a permission fixed manually is a
# permission that comes back wrong the next time the directory is recreated.
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

stamp="$(date -u +%Y%m%d-%H%M%S)"
name="business-${stamp}.dump"
target="${BACKUP_DIR}/${name}"

echo "==> Dumping ${POSTGRES_DB} to ${target}"

# The dump is written INSIDE the container to /backups, which compose.yaml
# bind-mounts to ./backups on the host. Streaming through stdout would work
# too, but a broken pipe mid-transfer leaves a truncated file that looks
# complete — writing to the mount and checking the exit status does not.
#
# (The hand-typed dump that preceded this script did exactly that, through a
# redirect. It was fine. It was fine by luck.)
docker compose exec -T "$DB_SERVICE" \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/backups/${name}"

if [ ! -s "$target" ]; then
    echo "FATAL: ${target} is missing or empty. The backup did NOT succeed." >&2
    exit 1
fi

# ── OWNERSHIP, THEN PERMISSIONS, AND IN THAT ORDER ──────────────────────────
#
# pg_dump runs inside the container, so the file lands owned by root as the
# container sees it. Mode 600 then makes it unreadable to whoever is running
# this script — fine when that is root, broken every other time. "Works only
# when run by root" is the kind of thing discovered during an incident.
#
# So the file is handed to whoever owns the backups directory on the host, and
# only then locked down. Both from inside the container, which is the only side
# that can: it is root there.
owner="$(stat -c '%u:%g' "$BACKUP_DIR")"
docker compose exec -T "$DB_SERVICE" chown "$owner" "/backups/${name}" >/dev/null 2>&1 || true
docker compose exec -T "$DB_SERVICE" chmod 600 "/backups/${name}" >/dev/null 2>&1 || true

# Every dump, not just tonight's — so one correct run repairs whatever earlier
# runs left wrong, instead of needing somebody to notice.
docker compose exec -T "$DB_SERVICE" \
    sh -c "chown ${owner} /backups/business-*.dump 2>/dev/null; \
           chmod 600 /backups/business-*.dump 2>/dev/null" \
    >/dev/null 2>&1 || true

# ── an encrypted COPY, for leaving the building ─────────────────────────────
#
# ══════════════════════════════════════════════════════════════════════════
# WHY THE LOCAL DUMP STAYS IN CLEAR AND ONLY THE COPY IS ENCRYPTED.
#
# This host holds the PUBLIC half of the key only — deliberately, so that
# taking the server does not hand over the archive of every earlier state of
# the database. But it means the host cannot decrypt, and therefore cannot run
# an automated restore test against an encrypted archive. Encrypting everything
# would quietly trade a working restore test for a stronger threat model, and
# an unverified backup is not a backup.
#
# So: the local archive stays plaintext at mode 600, where restore-test.sh
# verifies the real bytes; and a separate encrypted copy is made for anything
# that leaves this machine.
#
# The trade is honest. Local plaintext only matters to somebody who already has
# this host — and they already have the live database sitting next to it. The
# risk encryption actually addresses is a copy in somebody else's storage, on a
# stolen laptop, or in a bucket that turned out to be public. That is precisely
# the copy that is encrypted.
# ══════════════════════════════════════════════════════════════════════════

if [ -n "$BACKUP_RECIPIENT" ]; then
    mkdir -p "$OFFSITE_DIR"
    chmod 700 "$OFFSITE_DIR" 2>/dev/null || true

    echo "==> Encrypting a copy for off-box, to ${BACKUP_RECIPIENT}"
    if ! gpg --batch --yes --trust-model always \
             --recipient "$BACKUP_RECIPIENT" \
             --output "${OFFSITE_DIR}/${name}.gpg" --encrypt "$target"; then
        echo "FATAL: could not encrypt the off-box copy. The local dump is" >&2
        echo "       fine; nothing should leave this host until this works." >&2
        exit 1
    fi

    if [ ! -s "${OFFSITE_DIR}/${name}.gpg" ]; then
        echo "FATAL: gpg produced an empty file." >&2
        rm -f "${OFFSITE_DIR}/${name}.gpg"
        exit 1
    fi
    chmod 600 "${OFFSITE_DIR}/${name}.gpg"

    # The encrypted copy is the one that has to LEAVE, so it must be readable
    # by whatever collects it. gen-portal learned this the expensive way: gpg
    # writes as whoever runs the script, root under a timer, and four nights of
    # backups became uncollectable without anything failing loudly. Same owner
    # as the dump, from the same source of truth, so the two cannot drift.
    chown "$owner" "${OFFSITE_DIR}/${name}.gpg" 2>/dev/null || true
    chown "$owner" "$OFFSITE_DIR" 2>/dev/null || true
    find "$OFFSITE_DIR" -maxdepth 1 -name 'business-*.dump.gpg' -type f \
        -exec chown "$owner" {} + 2>/dev/null || true

    # ── prove it is addressed to the key we think it is ─────────────────────
    #
    # gpg encrypting "successfully" to the wrong key looks identical to
    # encrypting to the right one until somebody tries to open it. This reads
    # the packet header back and checks the recipient, which is the one part of
    # "can it be decrypted" a machine without the private key CAN check.
    #
    # The output is captured first and the EXIT STATUS ignored on purpose:
    # `--list-packets` also attempts a decrypt, so on this host it always ends
    # with "No secret key" and exits non-zero. That is the correct state here,
    # not a fault. Piping it into grep under `set -o pipefail` would turn an
    # expected failure into a fatal one on every run.
    packets="$(gpg --batch --list-packets "${OFFSITE_DIR}/${name}.gpg" 2>/dev/null || true)"
    if ! printf '%s' "$packets" | grep -qi "keyid ${BACKUP_RECIPIENT}"; then
        echo "FATAL: the encrypted copy is not addressed to ${BACKUP_RECIPIENT}." >&2
        rm -f "${OFFSITE_DIR}/${name}.gpg"
        exit 1
    fi

    echo "==> Off-box copy ready: ${OFFSITE_DIR}/${name}.gpg"
fi

# ── NO MEDIA ARCHIVE HERE, AND THAT IS CHECKED, NOT ASSUMED ─────────────────
# gen-portal's version also tars an uploads volume, because restoring its dump
# alone leaves attachment rows pointing at files that are not there. This
# application has no MEDIA_ROOT and no uploads volume — nothing is stored
# outside Postgres, so the dump is the whole of the data.
#
# ⚠ THE DAY THAT CHANGES, THIS SCRIPT SILENTLY STOPS BEING A FULL BACKUP. Add
#   the archive at the same time as the upload feature, not after the first
#   restore comes back with missing files.

size="$(du -h "$target" | cut -f1)"
echo "==> Wrote ${name} (${size})"

# ── retention ───────────────────────────────────────────────────────────────
# Deletes only files matching our own naming pattern, so an unrelated file
# somebody parked in this directory is never removed by a routine job.
echo "==> Pruning dumps older than ${RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -name 'business-*.dump' -type f \
     -mtime "+${RETENTION_DAYS}" -print -delete

# The encrypted copies are pruned on the SAME window. They are the ones that
# leave, so letting them accumulate here would slowly build a second, larger
# archive of everything — on the same disk, which is the problem they exist to
# solve.
if [ -d "$OFFSITE_DIR" ]; then
    find "$OFFSITE_DIR" -maxdepth 1 -name 'business-*.dump.gpg' -type f \
         -mtime "+${RETENTION_DAYS}" -print -delete
fi

count="$(find "$BACKUP_DIR" -maxdepth 1 -name 'business-*.dump' -type f | wc -l | tr -d ' ')"
echo "==> ${count} dump(s) retained in ${BACKUP_DIR}"

# ── OFF-BOX COPY ────────────────────────────────────────────────────────────
# Making the encrypted copy is not moving it. Nothing in THIS repository
# collects from ./backups/offsite yet — gen-portal has scripts/pull-backups.sh,
# run from the laptop that holds the private key, and it does not know about
# this directory.
#
# Until it does, every copy is on the same disk as the database it came from,
# and one failed volume loses both. Do not describe these backups as complete.
echo
if [ -z "$BACKUP_RECIPIENT" ]; then
    echo "WARNING: BACKUP_RECIPIENT is not set, so no encrypted copy was made"
    echo "         and there is nothing for an off-box pull to collect. Every"
    echo "         dump is on the same disk as the database it came from."
else
    offsite_count="$(find "$OFFSITE_DIR" -maxdepth 1 -name 'business-*.dump.gpg' -type f 2>/dev/null | wc -l | tr -d ' ')"
    echo "==> ${offsite_count} encrypted copy/copies waiting in ${OFFSITE_DIR}"
    echo
    echo "NOTE: making the copy is not moving it. NOTHING COLLECTS FROM HERE"
    echo "      YET — until something does, this is all on one disk."
fi
