"""
Every authentication decision in this application, in one module.

The same boundary gen-portal draws in `accounts/identity.py`, and for the same
reason: when something about authentication has to change — a lockout rule, a
session lifetime, the day staff sign-in moves behind a device check — it should
be one file to read and one file to test, not a behaviour spread across views.

Views call the functions here and do no reasoning of their own.
"""

from __future__ import annotations

import secrets

from django.db import transaction
from django.utils import timezone

from .models import (
    GENERIC_SIGN_IN_FAILURE,
    STAFF_SESSION_LIFETIME,
    PlatformAccount,
    StaffCredential,
    StaffSession,
    TenantMembership,
)

TOKEN_BYTES = 32
PREFIX_LENGTH = 12


class AuthError(Exception):
    """
    A refusal the caller is expected to render.

    `reason` is for our logs, `safe_message` is for the screen — and the safe
    message is IDENTICAL whatever went wrong, so a person at a till cannot
    learn which usernames exist by watching how the error changes.
    """

    def __init__(self, reason: str, safe_message: str = GENERIC_SIGN_IN_FAILURE):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = safe_message


# ── subscribers: Genmars accounts, arriving through sign-on ──────────────────


@transaction.atomic
def accept_genmars_account(payload: dict) -> PlatformAccount:
    """
    Turn a verified sign-on token response into a local account row.

    ⚠ WHAT IS DELIBERATELY THROWN AWAY.

    The payload carries `is_staff` and `staff_role`. They describe standing at
    GENMARS and are meaningless inside a customer's shop. They are not stored,
    not returned, and not consulted — if they were, every Genmars employee
    would silently hold authority in every tenant on this platform.

    `organisations` is also not read here. It is an onboarding hint for the
    screen that asks "is this the business you mean?", never a grant. Authority
    comes from TenantMembership and nowhere else.
    """
    account_data = payload.get("account") or {}
    account_id = account_data.get("id")
    if not account_id:
        raise AuthError("token_response_without_account", _SIGN_ON_FAILED)

    if not account_data.get("email_verified"):
        # gen-portal already refuses to issue a grant to an unverified address;
        # this is the belt to that braces. An unverified address is somebody
        # who has not yet proved they can read the mailbox.
        raise AuthError("email_not_verified", _SIGN_ON_FAILED)

    account, _ = PlatformAccount.objects.update_or_create(
        genmars_account_id=account_id,
        defaults={
            "email": account_data.get("email", ""),
            "full_name": account_data.get("full_name", "") or "",
        },
    )

    if account.is_blocked:
        raise AuthError("account_blocked", _SIGN_ON_FAILED)

    return account


def tenants_for(account: PlatformAccount):
    """Every tenant this subscriber may act in. The only source of that answer."""
    return TenantMembership.objects.select_related("organization").filter(
        account=account
    )


# ── operational staff: tenant-local, never Genmars ───────────────────────────


def authenticate_staff(*, organization_id: int, username: str, password: str):
    """
    Verify a cashier against ONE tenant. Returns the credential.

    The organisation is a parameter rather than something read from the
    username, so there is no such thing as a username that works across shops —
    and so a caller cannot reach another tenant's credential by guessing.

    Failures are uniform. The sequence below deliberately does the same work
    whether or not the username exists.
    """
    username = (username or "").strip()

    credential = (
        StaffCredential.objects.select_related("staff", "organization")
        .filter(organization_id=organization_id, username__iexact=username)
        .first()
    )

    if credential is None:
        # Burn a hash so a missing username is not measurably faster than a
        # wrong password. Same trick, same reason, as gen-portal.
        StaffCredential().check_password(password or "")
        raise AuthError("unknown_username")

    if credential.is_locked:
        raise AuthError("locked")

    if not credential.is_active:
        raise AuthError("inactive")

    if not credential.check_password(password or ""):
        credential.register_failure()
        raise AuthError("bad_password")

    credential.clear_failures()
    return credential


@transaction.atomic
def open_staff_session(credential: StaffCredential) -> tuple[StaffSession, str]:
    """
    Issue a session token. Returns (session, token) — the token is shown ONCE.

    Only the prefix and a hash are kept. Nothing in the database can be replayed
    as a session, which is what makes a leaked backup a smaller problem than it
    would otherwise be.
    """
    token = f"gbp_{secrets.token_urlsafe(TOKEN_BYTES)}"
    session = StaffSession.objects.create(
        credential=credential,
        token_prefix=token[:PREFIX_LENGTH],
        token_hash=_hash(token),
        expires_at=timezone.now() + STAFF_SESSION_LIFETIME,
    )
    return session, token


def resolve_staff_session(token: str) -> StaffSession | None:
    """
    Find the live session a token belongs to, or None.

    Looks up by prefix — an index — then verifies the hash. Comparing hashes
    across every row would be both slow and a timing signal.
    """
    token = (token or "").strip()
    if not token:
        return None

    candidates = StaffSession.objects.select_related(
        "credential", "credential__staff", "credential__organization"
    ).filter(token_prefix=token[:PREFIX_LENGTH], revoked_at__isnull=True)

    for session in candidates:
        if _verify(token, session.token_hash):
            if not session.is_live:
                return None
            if not session.credential.is_active:
                # Deactivating somebody must end the shift they are already in,
                # not merely stop the next sign-in. This is the whole point of
                # checking on every request rather than only at the door.
                return None
            session.last_used_at = timezone.now()
            session.save(update_fields=["last_used_at"])
            return session
    return None


def close_staff_session(session: StaffSession) -> None:
    session.revoked_at = timezone.now()
    session.save(update_fields=["revoked_at"])


def revoke_all_sessions(credential: StaffCredential) -> int:
    """Used when somebody leaves, or a password changes. Ends shifts in progress."""
    return StaffSession.objects.filter(
        credential=credential, revoked_at__isnull=True
    ).update(revoked_at=timezone.now())


# ── helpers ──────────────────────────────────────────────────────────────────

_SIGN_ON_FAILED = "That sign-in could not be completed. Start again."


def _hash(token: str) -> str:
    from django.contrib.auth.hashers import make_password

    return make_password(token)


def _verify(token: str, hashed: str) -> bool:
    from django.contrib.auth.hashers import check_password

    return check_password(token, hashed)
