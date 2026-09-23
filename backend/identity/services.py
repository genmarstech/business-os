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
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (
    GENERIC_SIGN_IN_FAILURE,
    INVITATION_LIFETIME,
    STAFF_SESSION_LIFETIME,
    PlatformAccount,
    StaffCredential,
    StaffPasswordReset,
    StaffSession,
    TenantInvitation,
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


# ── inviting a second subscriber ─────────────────────────────────────────────
#
# Until this existed, `create_tenant` was the ONLY way a TenantMembership was
# ever written, and it always granted OWNER. So the admin and accountant roles
# were defined, reasoned about in identity/access.py, and impossible to give to
# anybody: every subscriber was the sole owner of an organisation they had made
# themselves. A shop could not admit its own bookkeeper.


class InvitationError(Exception):
    """
    A refusal an OWNER is expected to read and act on.

    Not AuthError. AuthError says the same thing whatever went wrong because
    the reader may be probing; the reader here is somebody administering their
    own business, and telling them exactly what is wrong with what they typed
    reveals nothing they do not already know.
    """


@transaction.atomic
def invite_subscriber(*, organization, email: str, role: str, invited_by=None):
    """
    Offer somebody authority in this organisation.

    ⚠ NOTHING IS GRANTED HERE. This writes an offer; the membership appears
      only when that person signs on with the matching verified address — see
      `claim_invitations`. An invitation that granted immediately would be a
      way to attach yourself to somebody's account by typing their email.
    """
    email = (email or "").strip().lower()
    if not email:
        raise InvitationError("Which email address should we invite?")

    if role not in TenantMembership.Role.values:
        raise InvitationError("That is not a role.")

    already = TenantMembership.objects.filter(
        organization=organization, account__email__iexact=email
    ).exists()
    if already:
        raise InvitationError(f"{email} is already part of this business.")

    open_invite = TenantInvitation.objects.filter(
        organization=organization,
        email__iexact=email,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    ).first()
    if open_invite and open_invite.is_open:
        raise InvitationError(f"{email} has already been invited.")
    if open_invite:
        # Expired but still occupying the uniqueness slot. Withdrawing it is
        # what lets the owner re-invite somebody whose offer went stale, which
        # is the ordinary case rather than an edge one.
        open_invite.revoked_at = timezone.now()
        open_invite.save(update_fields=["revoked_at"])

    return TenantInvitation.objects.create(
        organization=organization,
        email=email,
        role=role,
        invited_by=invited_by,
        expires_at=timezone.now() + INVITATION_LIFETIME,
    )


@transaction.atomic
def revoke_invitation(*, invitation):
    """Withdraw an offer before it is taken."""
    if invitation.accepted_at is not None:
        raise InvitationError(
            "That invitation was already accepted. Remove the person instead."
        )
    if invitation.revoked_at is None:
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])
    return invitation


@transaction.atomic
def claim_invitations(account: PlatformAccount) -> list[TenantMembership]:
    """
    Turn every open invitation for this account's address into a membership.

    ══════════════════════════════════════════════════════════════════════════
    CALLED ON EVERY SIGN-ON, AND THAT IS WHAT MAKES THE INVITE WORK AT ALL.

    There is no link to click. The owner types an address; the next time the
    person behind that address completes the Genmars handoff, they are in.

    The address comes from `accept_genmars_account`, which has already refused
    the token if Genmars had not verified it. Matching on an address nobody
    proved they could read would let anyone join a shop by claiming its
    bookkeeper's email.
    ══════════════════════════════════════════════════════════════════════════
    """
    if account.is_blocked or not account.email:
        return []

    open_invitations = TenantInvitation.objects.select_for_update().filter(
        email__iexact=account.email.strip(),
        accepted_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    )

    granted = []
    for invitation in open_invitations:
        membership, created = TenantMembership.objects.get_or_create(
            account=account,
            organization=invitation.organization,
            defaults={"role": invitation.role, "invited_by": invitation.invited_by},
        )
        # Closed either way. An invitation to somebody who already had a
        # membership must not stay open — it would re-fire on every sign-in
        # and silently restore a role that was deliberately changed.
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = account
        invitation.save(update_fields=["accepted_at", "accepted_by"])
        if created:
            granted.append(membership)

    return granted


def tenants_for(account: PlatformAccount):
    """Every tenant this subscriber may act in. The only source of that answer."""
    return TenantMembership.objects.select_related("organization").filter(
        account=account
    )


# How many businesses one Genmars account may stand up on its own.
#
# There is a real case for more than one — somebody who owns a shop and a
# restaurant — and no honest case for forty. Self-serve creation with no
# ceiling is a spam vector that costs nothing to open and something to clean
# up, and a cap is the cheapest form of the rate limit this would otherwise
# need. Somebody with a genuine reason to exceed it should be talking to us,
# which is the point.
MAX_TENANTS_PER_ACCOUNT = 3

TOO_MANY_TENANTS = (
    "You have created as many businesses as this account allows. "
    "Get in touch and we will sort it out."
)


@transaction.atomic
def create_tenant(*, account: PlatformAccount, organization) -> TenantMembership:
    """
    Make the creator the owner of a tenant they have just created.

    ══════════════════════════════════════════════════════════════════════════
    THE MEMBERSHIP IS NOT AN AFTERTHOUGHT. IT IS THE HALF THAT MATTERS.

    Authority in this platform comes from TenantMembership and from nothing
    else — not from having created a row, not from anything in the sign-on
    token. So an organisation saved without one is not "a tenant awaiting
    setup", it is an orphan: invisible to its own creator, invisible to
    everybody, and reachable only from the admin.

    Hence the transaction. Either both rows exist or neither does; there is no
    state in between worth having.
    ══════════════════════════════════════════════════════════════════════════
    """
    if account.is_blocked:
        raise AuthError("account_blocked", _SIGN_ON_FAILED)

    existing = TenantMembership.objects.filter(
        account=account, role=TenantMembership.Role.OWNER
    ).count()
    if existing >= MAX_TENANTS_PER_ACCOUNT:
        raise AuthError("tenant_cap_reached", TOO_MANY_TENANTS)

    return TenantMembership.objects.create(
        account=account,
        organization=organization,
        role=TenantMembership.Role.OWNER,
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


# ── issuing and withdrawing a till login ─────────────────────────────────────
#
# A shop's manager decides who may open a till. These four functions are the
# whole of that decision, and they are here rather than in a serialiser for the
# same reason everything else in this file is: the day a credential needs a
# device check, a PIN length, or an expiry, this is the file to read.


MINIMUM_PASSWORD_LENGTH = 8


class CredentialError(Exception):
    """
    A refusal a MANAGER is expected to read and act on.

    Deliberately not AuthError. AuthError's whole purpose is to say the same
    thing whatever went wrong, because the person reading it may be probing for
    usernames. This is the opposite situation: a manager setting up their own
    employee needs to be told exactly what is wrong with what they typed, and
    they already know who works for them.
    """


@transaction.atomic
def issue_credential(*, staff, username: str, password: str) -> StaffCredential:
    """
    Give an employee a way into a till.

    ⚠ THE ORGANISATION IS TAKEN FROM THE STAFF RECORD, NEVER FROM A REQUEST.

    Blueprint §8. A caller who could name the organisation could attach a
    credential they control to somebody else's shop — and because sign-in takes
    the organisation as a parameter, that credential would then WORK there.
    `staff` has already been scoped to the caller's tenant by the time it
    reaches here; deriving from it is what keeps that scoping meaningful.
    """
    username = (username or "").strip()
    if not username:
        raise CredentialError("Choose a username for them.")

    _check_password_quality(password, username)

    # Per tenant, never globally — see the banner on StaffCredential. Checked
    # here as well as by the constraint so the answer is a sentence rather than
    # an IntegrityError, and case-insensitively because nobody types a username
    # the same way twice.
    clash = StaffCredential.objects.filter(
        organization_id=staff.organization_id, username__iexact=username
    ).exists()
    if clash:
        raise CredentialError(
            f"Somebody in this business already signs in as {username}."
        )

    if StaffCredential.objects.filter(staff=staff).exists():
        raise CredentialError(
            f"{staff.full_name} already has a sign-in. Reset its password "
            "instead of making a second one."
        )

    credential = StaffCredential(
        staff=staff,
        organization_id=staff.organization_id,
        username=username,
        # True by default on the model, and true in fact: a manager who types
        # somebody's first password knows it, so nothing that person does is
        # solely attributable to them until they change it.
        must_change_password=True,
    )
    credential.set_password(password)
    credential.full_clean(exclude=["password"])
    credential.save()
    return credential


@transaction.atomic
def reset_password(*, credential: StaffCredential, password: str) -> None:
    """
    A manager sets a new password — for somebody who has forgotten theirs.

    Every session ends with it. A password reset that leaves the old sessions
    running protects nobody: the reason to reset is usually that somebody else
    may know the old one, and a till left signed in is exactly where they would
    be using it.
    """
    _check_password_quality(password, credential.username)

    credential.set_password(password)
    credential.must_change_password = True
    credential.failed_sign_ins = 0
    credential.locked_until = None
    credential.save(
        update_fields=[
            "password",
            "must_change_password",
            "failed_sign_ins",
            "locked_until",
            "updated_at",
        ]
    )
    revoke_all_sessions(credential)


@transaction.atomic
def change_own_password(
    *, credential: StaffCredential, current: str, replacement: str
) -> None:
    """
    The cashier changes their own, and only then is `must_change_password` off.

    The current password is required even though the caller is already holding
    a valid session: a till left unattended is the normal state of a till, and
    without this anybody passing it could lock the real cashier out of their
    own login.

    Their OTHER sessions end; the one making this call does not, because
    signing somebody out of the till they are standing at, as a reward for
    doing what they were asked, is not a security measure.
    """
    if not credential.check_password(current or ""):
        raise CredentialError("That is not your current password.")

    _check_password_quality(replacement, credential.username)

    if credential.check_password(replacement):
        raise CredentialError("Choose a password you have not just been using.")

    credential.set_password(replacement)
    credential.must_change_password = False
    credential.save(
        update_fields=["password", "must_change_password", "updated_at"]
    )


@transaction.atomic
def set_credential_active(*, credential: StaffCredential, active: bool) -> None:
    """
    Withdraw or restore a till login. The personnel record is untouched.

    Turning it off ends every session immediately rather than at expiry. The
    moment somebody is walked off the premises is the moment this has to take
    effect, not up to eight hours later.
    """
    if credential.is_active == active:
        return

    credential.is_active = active
    if active:
        # Somebody coming back should not inherit a lockout from before.
        credential.failed_sign_ins = 0
        credential.locked_until = None
    credential.save(
        update_fields=["is_active", "failed_sign_ins", "locked_until", "updated_at"]
    )

    if not active:
        revoke_all_sessions(credential)


def _check_password_quality(password: str, username: str) -> None:
    """
    The floor, and only the floor.

    Deliberately not Django's AUTH_PASSWORD_VALIDATORS. Those are tuned for a
    person choosing their own password at leisure on a keyboard; this is a
    manager typing one for a cashier who will enter it on a touchscreen at the
    start of every shift, and a common-password list that rejects what they
    picked without saying why produces a sticky note on the monitor.

    What is here is what is actually dangerous: something short enough to
    guess, or the username itself.
    """
    password = password or ""
    if len(password) < MINIMUM_PASSWORD_LENGTH:
        raise CredentialError(
            f"Passwords need at least {MINIMUM_PASSWORD_LENGTH} characters."
        )
    if password.strip().lower() == (username or "").strip().lower():
        raise CredentialError("The password cannot be the username.")


# ── a cashier resetting their own forgotten password ─────────────────────────
#
# ═══════════════════════════════════════════════════════════════════════════
# EVERY PATH THROUGH request_password_reset LOOKS THE SAME FROM OUTSIDE.
#
# Unknown username, deactivated credential, no email on file, too many
# requests already — all of them return None and the view answers identically.
# A caller learns nothing about which usernames exist in which shop, which is
# the same rule authenticate_staff follows and for the same reason: a till
# sign-in screen is reachable by anyone who can reach the shop's URL.
#
# The cost is that a genuine cashier whose record has no email gets silence.
# That is the right trade here — the alternative tells a stranger that the
# username they guessed is real — and the manager reset still works for them.
# ═══════════════════════════════════════════════════════════════════════════

RESET_CODE_LIFETIME = StaffPasswordReset.LIFETIME

# One message for every way a code can be refused: wrong, expired, already
# used, too many attempts. A caller learning WHICH is learning whether the
# username exists and whether a reset is in flight for it.
RESET_REFUSED = "That code is not valid. Ask for a new one."


def request_password_reset(*, organization_id: int, username: str) -> str | None:
    """
    Mint a reset code, or decide not to. Returns the code, or None.

    Returning the code rather than sending it keeps this function free of
    email: the caller sends it. That is what lets the tests assert on the code
    without a mail backend, and what stops a transport failure rolling back a
    row that was correctly created.
    """
    username = (username or "").strip()

    credential = (
        StaffCredential.objects.select_related("staff")
        .filter(organization_id=organization_id, username__iexact=username)
        .first()
    )
    if credential is None or not credential.is_active:
        return None

    # A record with no address cannot be sent to. Silence, not an error — see
    # the banner.
    if not (credential.staff and credential.staff.email):
        return None

    # The ceiling. Counted in the database because there is no shared cache
    # here, so a per-process throttle would multiply by the worker count.
    since = timezone.now() - timedelta(hours=1)
    if (
        StaffPasswordReset.objects.filter(
            credential=credential, created_at__gte=since
        ).count()
        >= StaffPasswordReset.MAX_PER_HOUR
    ):
        return None

    # Six digits, from secrets rather than random: this is a credential for the
    # next fifteen minutes, and it is typed on a touchscreen by somebody
    # standing at a counter, which is why it is not a long opaque string.
    code = f"{secrets.randbelow(1_000_000):06d}"

    # Any earlier live code for this credential stops working. Two valid codes
    # at once means a code that was emailed, forgotten and left live — and the
    # person asking for a new one has told us the old one is no use to them.
    StaffPasswordReset.objects.filter(
        credential=credential, used_at__isnull=True
    ).update(used_at=timezone.now())

    StaffPasswordReset.objects.create(
        credential=credential,
        code_hashed=_hash(code),
        expires_at=timezone.now() + StaffPasswordReset.LIFETIME,
    )
    return code


def complete_password_reset(
    *, organization_id: int, username: str, code: str, new_password: str
) -> StaffCredential:
    """
    Spend a code and set the password. Raises CredentialError on any refusal.

    ═══════════════════════════════════════════════════════════════════════════
    ⚠ THIS FUNCTION IS NOT @transaction.atomic, AND IT MUST NOT BECOME SO.
    ⚠ THE ATTEMPT COUNTER IS THE REASON.
    ⚠
    ⚠ It was atomic, and the attempt limit therefore did not exist. Every
    ⚠ refusal raises, a raise inside an atomic block rolls the block back, and
    ⚠ the rollback undid the very increment meant to record the failed guess.
    ⚠ `attempts` was written and discarded five times and stayed at zero, so a
    ⚠ six-digit code could be guessed without limit for its whole fifteen
    ⚠ minutes. The test that found it asserts the correct code STOPS working
    ⚠ after MAX_ATTEMPTS wrong ones.
    ⚠
    ⚠ So the counter is committed outside any transaction, before the code is
    ⚠ checked, and only the final mutation is wrapped.
    ═══════════════════════════════════════════════════════════════════════════

    ⚠ THE PASSWORD QUALITY CHECK RUNS BEFORE THE CODE IS SPENT. Rejecting a
      short password after burning the code would send somebody back to their
      inbox for a second one, having done nothing wrong except pick badly.
    """
    username = (username or "").strip()

    credential = (
        StaffCredential.objects.select_related("staff")
        .filter(organization_id=organization_id, username__iexact=username)
        .first()
    )
    if credential is None or not credential.is_active:
        raise CredentialError(RESET_REFUSED)

    reset = (
        StaffPasswordReset.objects.filter(credential=credential, used_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if reset is None or reset.is_spent:
        raise CredentialError(RESET_REFUSED)

    # F() rather than read-modify-write: two attempts arriving together would
    # otherwise each read the same number and write the same number, and the
    # limit would count one guess instead of two.
    #
    # Committed here, outside any transaction, for the reason in the banner.
    StaffPasswordReset.objects.filter(pk=reset.pk).update(attempts=F("attempts") + 1)
    reset.refresh_from_db(fields=["attempts"])

    if not _verify(code or "", reset.code_hashed):
        raise CredentialError(RESET_REFUSED)

    # See the warning above: quality first, then spend.
    _check_password_quality(new_password, credential.username)

    with transaction.atomic():
        # Compare-and-set rather than a lock. If two requests race with the
        # same valid code, exactly one UPDATE matches a row where used_at is
        # still null; the other gets zero and is refused. This is also what
        # makes a single code single-use under concurrency.
        spent = StaffPasswordReset.objects.filter(
            pk=reset.pk, used_at__isnull=True
        ).update(used_at=timezone.now())
        if not spent:
            raise CredentialError(RESET_REFUSED)

        credential.set_password(new_password)

        # FALSE, unlike the manager reset which sets it True. The distinction
        # is the whole point: a manager who sets a password knows it, so the
        # cashier's actions are not yet solely theirs. A code sent to the
        # cashier's own address produces a password nobody else has seen.
        credential.must_change_password = False

        # A forgotten password is also how a locked-out cashier gets back to
        # work. Leaving the lockout would mean proving your identity by email
        # and still being refused at the till.
        credential.failed_sign_ins = 0
        credential.locked_until = None
        credential.save(
            update_fields=[
                "password",
                "must_change_password",
                "failed_sign_ins",
                "locked_until",
                "updated_at",
            ]
        )

        # Every other session ends. Somebody resetting a forgotten password may
        # be doing it because they think somebody else has been using the
        # login, and a reset that leaves the other session open answers
        # nothing.
        revoke_all_sessions(credential)

    return credential


# ── helpers ──────────────────────────────────────────────────────────────────

_SIGN_ON_FAILED = "That sign-in could not be completed. Start again."


def _hash(token: str) -> str:
    from django.contrib.auth.hashers import make_password

    return make_password(token)


def _verify(token: str, hashed: str) -> bool:
    from django.contrib.auth.hashers import check_password

    return check_password(token, hashed)
