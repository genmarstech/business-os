"""
Who is allowed in, and on whose authority.

═══════════════════════════════════════════════════════════════════════════════
THERE ARE TWO KINDS OF PERSON HERE, AND THEY ARE NOT THE SAME KIND OF PROBLEM.

  SUBSCRIBER   the owner, an org admin, an accountant — whoever holds the
               commercial relationship with Genmars. They sign in with a
               GENMARS ACCOUNT, through the sign-on handoff. This application
               never sees, sets or stores their password, and `PlatformAccount`
               has no field that could hold one.

  OPERATIONAL  a cashier, a branch manager, a stock clerk. They work for the
               CUSTOMER, not for Genmars. They are tenant data, like a Branch
               or a Register, and they authenticate HERE.

The line between them is who the commercial relationship is with. It was drawn
deliberately on 2026-09-21 and the reasoning is in
`internals-tm/docs/BUSINESS-PLATFORM-ARCHITECTURE.md`; the short version is
that a shop must be able to sack a cashier at 9pm on a Saturday without
telephoning Genmars, and an offline register cannot refresh a Genmars session.
═══════════════════════════════════════════════════════════════════════════════

⚠ THE TWO CREDENTIAL STORES MUST NEVER MEET.

A `StaffCredential` password must never be accepted by `api.genmars.co.ke`, and
a Genmars password must never be accepted here. No shared hash, no shared
table, and no endpoint that tries one and then the other. The moment something
accepts either, a cashier at one shop becomes a way into the Genmars portal.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from organisations.models import BusinessOrganization, OrganizationStaff

# Matches gen-portal's accounts/identity.py. A cashier locked out of a till in
# the middle of a queue is a real cost, so the lock expires on its own rather
# than needing a manager — the same reasoning, and the same numbers, as the
# portal uses for clients.
MAX_FAILED_SIGN_INS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

# A shift, with room either side. Long enough that nobody re-authenticates
# mid-queue; short enough that a terminal left on overnight is not a standing
# door in the morning.
STAFF_SESSION_LIFETIME = timedelta(hours=14)

# Shown for every failed staff sign-in, whatever actually went wrong. A message
# that distinguishes "no such user" from "wrong password" tells somebody
# standing at a till which usernames exist in this shop.
GENERIC_SIGN_IN_FAILURE = "That username and password do not match."


class PlatformAccount(models.Model):
    """
    A Genmars identity, mirrored locally so other rows can point at it.

    ⚠ THIS MODEL HAS NO PASSWORD FIELD AND MUST NEVER GROW ONE.

    It is not an account in the sense of something you sign into. It is a
    foreign-key target: memberships, audit entries and ownership need something
    stable to reference, and referencing a remote integer everywhere would mean
    no database-level integrity at all.

    Everything authoritative about this person — whether they still exist,
    whether they are active, what their email is — belongs to gen-portal and is
    refreshed on each sign-in. `is_blocked` is the one local decision: it bars
    somebody from THIS platform without pretending to touch their Genmars
    account, which is not ours to disable.
    """

    genmars_account_id = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        help_text="`account.id` from the sign-on token. The join key.",
    )
    email = models.EmailField(
        help_text="As of the last sign-in. gen-portal is the source of truth."
    )
    full_name = models.CharField(max_length=200, blank=True, default="")

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    is_blocked = models.BooleanField(
        default=False,
        help_text=(
            "Barred from this platform. Says nothing about their Genmars "
            "account, which this application does not get to disable."
        ),
    )

    class Meta:
        ordering = ["email"]
        verbose_name = "Platform account"

    def __str__(self) -> str:
        return self.email

    @property
    def is_authenticated(self) -> bool:
        """DRF asks principals this. A row that exists has been signed in."""
        return True


class TenantMembership(models.Model):
    """
    Which subscriber may act in which tenant, and with what authority.

    ══════════════════════════════════════════════════════════════════════════
    THIS TABLE IS THE ONLY ANSWER TO "MAY THEY?".

    The sign-on token also carries an `organisations` array — the caller's
    memberships at GENMARS. That is a useful onboarding hint ("you already deal
    with us as Kilimani Dental, is this that business?") and it is **not** a
    grant of anything here. A tenant is entered because a row exists in this
    table, and for no other reason.

    Nor is `account.is_staff` from that token. It describes somebody's standing
    at Genmars and means nothing inside a customer's shop; granting on it would
    hand every Genmars employee the till of every customer.
    ══════════════════════════════════════════════════════════════════════════
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Organisation admin"
        ACCOUNTANT = "accountant", "Accountant"

    account = models.ForeignKey(
        PlatformAccount, on_delete=models.CASCADE, related_name="memberships"
    )
    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="subscribers"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.ADMIN)

    invited_by = models.ForeignKey(
        PlatformAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invitations_sent",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["organization__name", "role"]
        constraints = [
            models.UniqueConstraint(
                fields=["account", "organization"], name="one_membership_per_tenant"
            )
        ]

    def __str__(self) -> str:
        return f"{self.account.email} — {self.organization.name} ({self.role})"


class StaffCredential(models.Model):
    """
    A cashier's way in. Tenant-local, and never a Genmars account.

    ── WHY THIS IS SEPARATE FROM OrganizationStaff ─────────────────────────────

    `OrganizationStaff` is a personnel record — name, phone, KRA PIN, start
    date. Most of that is filled in by the shop's manager and some of it is
    sensitive. Whether that person can SIGN IN is a different question with a
    different lifecycle: staff exist before they get a login and after they lose
    one, and a row here is an explicit, auditable answer to "can they open a
    till today".

    Keeping them apart also means a screen that lists employees does not have a
    password hash in its queryset.

    ── USERNAMES ARE UNIQUE PER TENANT, NOT GLOBALLY ───────────────────────────

    Two shops may both employ a `jmwangi`. Making this globally unique would
    not only be wrong, it would leak: the error returned when creating one shop's
    cashier would reveal that the name is taken in SOMEBODY ELSE'S shop. A
    uniqueness constraint is an oracle, and tenant isolation has to survive it.

    `organization` is denormalised from `staff.organization` so the constraint
    can exist at all; `clean()` refuses to let the two disagree.
    """

    staff = models.OneToOneField(
        OrganizationStaff, on_delete=models.CASCADE, related_name="credential"
    )
    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="staff_credentials"
    )

    username = models.CharField(max_length=60)
    password = models.CharField(max_length=128)

    must_change_password = models.BooleanField(
        default=True,
        help_text=(
            "A manager sets the first password, so they know it. Until it is "
            "changed, the cashier's actions are not solely attributable to them."
        ),
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Turned off when somebody leaves. The personnel record stays.",
    )

    failed_sign_ins = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization__name", "username"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "username"],
                name="one_username_per_organization",
            )
        ]

    def __str__(self) -> str:
        return f"{self.username}@{self.organization.name}"

    def clean(self) -> None:
        if self.staff_id and self.organization_id:
            if self.staff.organization_id != self.organization_id:
                raise ValidationError(
                    "A credential must belong to the same organisation as the "
                    "staff record it is for."
                )

    def save(self, *args, **kwargs):
        # Denormalised field, kept true without asking the caller to remember.
        if self.staff_id and not self.organization_id:
            self.organization_id = self.staff.organization_id
        super().save(*args, **kwargs)

    # ── the credential itself ───────────────────────────────────────────────

    def set_password(self, raw: str) -> None:
        """Hashes with whatever PASSWORD_HASHERS says — Argon2 in this project."""
        self.password = make_password(raw)

    def check_password(self, raw: str) -> bool:
        return check_password(raw or "", self.password)

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    def register_failure(self) -> None:
        self.failed_sign_ins += 1
        if self.failed_sign_ins >= MAX_FAILED_SIGN_INS:
            self.locked_until = timezone.now() + LOCKOUT_DURATION
            self.failed_sign_ins = 0
        self.save(update_fields=["failed_sign_ins", "locked_until", "updated_at"])

    def clear_failures(self) -> None:
        if self.failed_sign_ins or self.locked_until:
            self.failed_sign_ins = 0
            self.locked_until = None
            self.save(update_fields=["failed_sign_ins", "locked_until", "updated_at"])


class StaffSession(models.Model):
    """
    A signed-in till.

    ── THE TOKEN IS STORED HASHED, WITH A SHORT CLEAR PREFIX ───────────────────

    The same shape gen-portal uses for `SystemKey`: the prefix is a lookup
    index, the hash is the secret. A database read — a backup, a support query,
    a leaked dump — yields nothing that can be presented as a session.

    ── WHY A TOKEN AND NOT A COOKIE ────────────────────────────────────────────

    A till is not a browser tab. It is a fixed terminal that stays signed in for
    a shift, may be a native or offline-capable client later (blueprint §11),
    and needs a credential the client holds explicitly rather than one the
    platform sets invisibly.
    """

    credential = models.ForeignKey(
        StaffCredential, on_delete=models.CASCADE, related_name="sessions"
    )

    token_prefix = models.CharField(max_length=12, db_index=True)
    token_hash = models.CharField(max_length=128)

    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return f"{self.credential.username} · {self.issued_at:%Y-%m-%d %H:%M}"

    @property
    def is_live(self) -> bool:
        now = timezone.now()
        return self.revoked_at is None and self.expires_at > now
