"""
Taking money by M-Pesa, at a shop's own till.

═══════════════════════════════════════════════════════════════════════════════
THESE ARE THE TENANT'S PAYMENTS, NOT GENMARS'.

A shop's customer pays the SHOP, using the shop's own paybill or buy-goods
number and the shop's own Daraja application. Genmars initiates the request on
their behalf and is told the outcome; no money passes through Genmars and no
Genmars credential is involved.

What a tenant pays GENMARS for their subscription is a different flow
entirely, brokered by gen-portal where the Invoice and the company's own
M-Pesa credentials already live — see the banner in `subscriptions/services.py`.
Keeping the two apart is the whole reason this app holds no Genmars secret.
═══════════════════════════════════════════════════════════════════════════════

── THE STK QUERY IS THE SOURCE OF TRUTH. THE CALLBACK IS A HINT. ───────────────

Safaricom POSTs the result of a push to a URL we publish, and that URL is
necessarily PUBLIC — a payment processor cannot hold a session. Anybody can
post to it. If a callback saying "paid" were enough to mark a sale paid, the
endpoint would be a way to walk out with the stock.

So a callback never decides anything. It records that something happened and
prompts us to ask Safaricom directly, through the STK query API, and only
Safaricom's own answer moves a push to PAID. A forged callback therefore
achieves nothing except an extra query.

It also makes the feature work when the callback never arrives, which happens
often enough to design for: the till's polling asks Safaricom on its own
schedule and does not need the callback at all. The callback only makes the
answer arrive sooner.
"""

from __future__ import annotations

import hashlib
import secrets
from decimal import Decimal

from django.db import models
from django.utils import timezone

from branches.models import Branches
from organisations.models import BusinessOrganization

MONEY = {"max_digits": 12, "decimal_places": 2}
ZERO = Decimal("0.00")


def _callback_token() -> str:
    """Unguessable, per push. 32 bytes is far beyond brute force over HTTPS."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """
    What we store instead of the token itself.

    The token is a bearer credential for one push's callback. Storing the
    digest means a database read does not hand somebody a working callback
    URL — the same reasoning as `SystemKey` in gen-portal, which keeps a
    hash and a short clear prefix.
    """
    return hashlib.sha256(token.encode()).hexdigest()


class MpesaTill(models.Model):
    """
    One shop's M-Pesa configuration.

    ── ONE PER ORGANISATION, NOT PER BRANCH ────────────────────────────────
    Most shops have a single paybill or till number across every branch, and
    a sale already records which branch took it, so per-branch reconciliation
    works without per-branch configuration. Chains that genuinely run a
    separate till per branch are a later addition — and a real one, which is
    why `StkPush.branch` is recorded now rather than being added when it is
    needed.

    ── THE CREDENTIALS ARE SEALED, AND READ IN EXACTLY ONE PLACE ───────────
    `consumer_key`, `consumer_secret` and `passkey` hold ciphertext. Nothing
    reads them but `payments/daraja.py`, through `credentials()` below, and
    nothing ever returns them over the API — see the serializer, which is
    write-only on all three.
    """

    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox (test)"
        PRODUCTION = "production", "Production (real money)"

    class TransactionType(models.TextChoices):
        # Safaricom's own names. Paybill wants an account reference; buy-goods
        # does not, and sending one is harmless but meaningless.
        PAYBILL = "CustomerPayBillOnline", "Paybill"
        BUY_GOODS = "CustomerBuyGoodsOnline", "Buy goods (till)"

    organization = models.OneToOneField(
        BusinessOrganization, on_delete=models.CASCADE, related_name="mpesa_till"
    )

    environment = models.CharField(
        max_length=12,
        choices=Environment.choices,
        default=Environment.SANDBOX,
        help_text=(
            "Sandbox until the shop has tested it. Production moves real "
            "money out of real customers' accounts."
        ),
    )

    short_code = models.CharField(
        max_length=12,
        help_text="The paybill or till number a customer would normally pay.",
    )
    transaction_type = models.CharField(
        max_length=24,
        choices=TransactionType.choices,
        default=TransactionType.PAYBILL,
    )

    # ── A BUY-GOODS TILL IS TWO NUMBERS, AND SENDING ONE SILENTLY FAILS ─────
    #
    # ⚠ THIS FIELD IS WHY BUY-GOODS WORKS AT ALL. Do not collapse it back
    #   into `short_code` on the grounds that they are usually equal.
    #
    # Daraja asks a paybill for ONE number and a buy-goods till for TWO, and
    # it does not say so in the error:
    #
    #            BusinessShortCode   PartyB        Password built from
    #   paybill  the paybill         the paybill   the paybill
    #   goods    the STORE number    the till      the STORE number
    #
    # The store number — Safaricom also call it the head office number — is
    # what the passkey was issued against. Send the till number in its place
    # and the password hashes against the wrong shortcode, so Daraja answers
    # "invalid access token" or "Bad Request" and a cashier is told M-Pesa
    # refused the request with nothing to act on.
    #
    # Blank means "the same as short_code", which is the truth for every
    # paybill and for a buy-goods till whose two numbers genuinely match. It
    # is also what every row written before this field existed means, so the
    # migration needs no data step and paybill behaviour is unchanged.
    store_number = models.CharField(
        max_length=12,
        blank=True,
        help_text=(
            "Buy-goods only: the store or head office number the passkey was "
            "issued against. Blank uses the till number for both."
        ),
    )
    account_reference = models.CharField(
        max_length=24,
        blank=True,
        help_text=(
            "What appears on the customer's statement for a paybill. Ignored "
            "for buy-goods, which has no account."
        ),
    )

    # ── SEALED. See payments/crypto.py. ─────────────────────────────────────
    consumer_key_sealed = models.TextField(blank=True)
    consumer_secret_sealed = models.TextField(blank=True)
    passkey_sealed = models.TextField(blank=True)

    is_active = models.BooleanField(
        default=False,
        help_text=(
            "Off until the shop turns it on. A half-entered configuration "
            "that silently starts charging customers is the thing this "
            "prevents."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "M-Pesa till"
        verbose_name_plural = "M-Pesa tills"

    def __str__(self) -> str:
        return f"{self.organization.name} — {self.short_code}"

    # ── the two numbers a push is addressed with ────────────────────────────
    #
    # Derived here rather than in `daraja.py` so there is ONE answer to "which
    # shortcode" and both the push and the query it is settled by are built
    # from it. They were two literals before, and a query that asks about a
    # different shortcode than the push was sent under is a payment nobody can
    # confirm.

    @property
    def push_short_code(self) -> str:
        """
        `BusinessShortCode`, and the number the password is built from.

        The store number for a buy-goods till, the paybill otherwise. See the
        table on `store_number` for why those differ.
        """
        if self.is_buy_goods and self.store_number:
            return self.store_number
        return self.short_code

    @property
    def push_party_b(self) -> str:
        """`PartyB` — who is actually paid. Always the number on the shop."""
        return self.short_code

    @property
    def is_buy_goods(self) -> bool:
        return self.transaction_type == self.TransactionType.BUY_GOODS

    # ── reading and writing the secrets ─────────────────────────────────────

    def set_credentials(self, *, consumer_key=None, consumer_secret=None,
                        passkey=None) -> None:
        """
        Seal and store. `None` leaves a value alone, so a form can change the
        short code without being made to retype three secrets it was never
        shown.
        """
        from . import crypto

        if consumer_key is not None:
            self.consumer_key_sealed = crypto.seal(consumer_key)
        if consumer_secret is not None:
            self.consumer_secret_sealed = crypto.seal(consumer_secret)
        if passkey is not None:
            self.passkey_sealed = crypto.seal(passkey)

    def credentials(self) -> dict[str, str]:
        """
        ⚠ RETURNS PLAINTEXT SECRETS. Call it in `daraja.py` and nowhere else.

        Deliberately a method rather than three decrypting properties: an
        attribute that quietly decrypts is an attribute that ends up in a
        `repr`, a log line or a serialiser by accident. A call this explicit
        is one somebody has to mean.
        """
        from . import crypto

        return {
            "consumer_key": crypto.unseal(self.consumer_key_sealed),
            "consumer_secret": crypto.unseal(self.consumer_secret_sealed),
            "passkey": crypto.unseal(self.passkey_sealed),
        }

    @property
    def is_complete(self) -> bool:
        """Whether there is enough here to attempt a push at all."""
        return bool(
            self.short_code
            and self.consumer_key_sealed
            and self.consumer_secret_sealed
            and self.passkey_sealed
        )

    @property
    def is_live(self) -> bool:
        return self.environment == self.Environment.PRODUCTION


class StkPush(models.Model):
    """
    One request for a customer to approve a payment, and what became of it.

    ── IT FUNDS EXACTLY ONE SALE ───────────────────────────────────────────
    `sale` is a OneToOne. A confirmed payment that could be attached to two
    baskets is one customer paying for somebody else's shopping, and it is
    the kind of mistake a busy till makes by accident long before anybody
    does it on purpose. The database refuses it.

    ── AND THE SALE IS WRITTEN AFTER THE MONEY, NOT BEFORE ─────────────────
    The till requests a push, waits, and only rings the sale up once the
    payment is confirmed. Creating the sale first would mean voiding one
    every time a customer changes their mind at the PIN prompt, and a void
    is a thing a manager has to explain.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for the customer"
        PAID = "paid", "Paid"
        FAILED = "failed", "Not paid"
        EXPIRED = "expired", "Timed out"

    SETTLED = frozenset({Status.PAID, Status.FAILED, Status.EXPIRED})

    organization = models.ForeignKey(
        BusinessOrganization, on_delete=models.CASCADE, related_name="stk_pushes"
    )
    # Denormalised like Sale.branch: a push belongs where it was taken, and
    # §8 scoping wants one hop rather than two.
    branch = models.ForeignKey(
        Branches, on_delete=models.PROTECT, related_name="stk_pushes"
    )

    amount = models.DecimalField(**MONEY)
    # Stored normalised to 2547…, which is the only form Daraja accepts. The
    # cashier types whatever the customer says.
    phone_number = models.CharField(max_length=15)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.REQUESTED
    )

    # What Safaricom calls it. `checkout_request_id` is the handle the query
    # API takes, so it is the one that matters.
    merchant_request_id = models.CharField(max_length=64, blank=True)
    checkout_request_id = models.CharField(max_length=64, blank=True, db_index=True)

    # The customer's receipt — the code they can read back to the cashier.
    mpesa_receipt = models.CharField(max_length=32, blank=True)
    result_code = models.CharField(max_length=8, blank=True)
    result_description = models.CharField(max_length=255, blank=True)

    # ── THE CALLBACK CREDENTIAL ─────────────────────────────────────────────
    # Hashed, never stored in the clear. The token itself exists only in the
    # URL handed to Safaricom at request time and is not recoverable from
    # here, which is the point.
    callback_token_digest = models.CharField(max_length=64, unique=True)
    callback_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When a callback arrived. Recorded for diagnosis only — the "
            "status is decided by asking Safaricom, never by the callback."
        ),
    )

    sale = models.OneToOneField(
        "sales.Sale",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stk_push",
    )

    requested_by_staff = models.ForeignKey(
        "organisations.OrganizationStaff",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stk_pushes",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "status"])]
        constraints = [
            # A receipt number is Safaricom's own identifier for a
            # transaction. Two rows carrying one means we have recorded the
            # same payment twice, and the second is funding a sale nobody
            # paid for.
            models.UniqueConstraint(
                fields=["organization", "mpesa_receipt"],
                condition=~models.Q(mpesa_receipt=""),
                name="unique_mpesa_receipt_per_organization",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} from {self.phone_number} — {self.get_status_display()}"

    @property
    def is_settled(self) -> bool:
        return self.status in self.SETTLED

    @property
    def is_spent(self) -> bool:
        """Already funding a sale. A confirmed payment is spent once."""
        return self.sale_id is not None

    @staticmethod
    def new_token() -> str:
        return _callback_token()
