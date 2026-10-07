"""
The only place an M-Pesa payment is written.

── THE SHAPE OF ONE PAYMENT ────────────────────────────────────────────────────

    cashier asks ──▶ request()  ──▶ Safaricom prompts the customer
                        │
          callback ─────┤  (a hint; decides nothing)
          till polls ───┤
                        ▼
                    confirm()  ──▶ asks Safaricom  ──▶ PAID / FAILED
                                                          │
                                        the till rings the sale up, and
                                        checkout() spends the push once

── WHY THE AMOUNT MUST BE WHOLE SHILLINGS ──────────────────────────────────────

M-Pesa moves shillings; it has no concept of cents. A basket of 150.50 can be
pushed as 150 or as 151 and neither is right: the first leaves the drawer 50
cents short on every such sale, which compounds and never reconciles, and the
second charges a customer more than the receipt says.

So a non-whole total is REFUSED, with a message telling the cashier to adjust
the basket. A shop that wants to round can discount the 50 cents, which is a
decision recorded on the sale rather than one made silently by this file.
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from notifications import services as notifications

from . import daraja
from .crypto import NotConfigured
from .models import MpesaTill, StkPush, token_digest

# Daraja's own code for a request the customer has not answered yet.
PENDING_CODES = {"1032-pending", "500.001.1001"}

# How long a push is worth waiting for. Safaricom gives the customer about a
# minute; past this the till stops asking and the cashier takes cash.
EXPIRE_AFTER_SECONDS = 180


class PaymentError(ValidationError):
    """Refused for a reason a cashier can act on."""


def normalise_phone(raw: str) -> str:
    """
    Whatever the customer said, as Daraja wants it: 2547XXXXXXXX.

    A cashier types what they hear — "0712 345 678", "+254 712 345678",
    "712345678". All three are the same phone and all three must work, or the
    feature fails at the one moment it is being used in a hurry.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("254"):
        trimmed = digits
    elif digits.startswith("0"):
        trimmed = "254" + digits[1:]
    elif len(digits) == 9:
        trimmed = "254" + digits
    else:
        trimmed = digits

    if not re.fullmatch(r"254[17]\d{8}", trimmed):
        raise PaymentError(
            {"phone_number": "That is not a Safaricom or Airtel number. "
                             "It should look like 0712 345 678."}
        )
    return trimmed


def whole_shillings(amount: Decimal) -> int:
    """The amount, or a refusal. See the banner on why there is no rounding."""
    if amount <= 0:
        raise PaymentError({"amount": "There is nothing to pay."})
    if amount != amount.to_integral_value():
        raise PaymentError(
            {
                "amount": (
                    f"M-Pesa cannot take cents, and this comes to "
                    f"{amount}. Adjust the basket to a whole shilling."
                )
            }
        )
    return int(amount)


def till_for(organization_id: int) -> MpesaTill:
    till = MpesaTill.objects.filter(organization_id=organization_id).first()
    if till is None or not till.is_active:
        raise PaymentError(
            {"detail": "M-Pesa is not set up for this business yet. "
                       "An owner turns it on under Settings."}
        )
    if not till.is_complete:
        raise PaymentError(
            {"detail": "The M-Pesa settings are incomplete. An owner finishes "
                       "them under Settings."}
        )
    return till


def request(*, branch, amount: Decimal, phone: str, callback_base: str,
            actor=None, description: str = "Payment") -> StkPush:
    """
    Ask the customer to approve a payment. Writes the push, then calls out.

    The row is written BEFORE Safaricom is called, so a response that arrives
    while the process is dying still has somewhere to land — and so the
    callback token exists before the URL carrying it is handed over.

    ── DELIBERATELY NOT @transaction.atomic ────────────────────────────────
    It was, and that quietly defeated the paragraph above: raising
    PaymentError when Daraja refuses rolled the whole transaction back,
    including the FAILED row the except branch had just written. The attempt
    vanished, so a shop whose M-Pesa was misconfigured saw a cashier press
    the button ten times and had nothing recording that it had happened.

    Only the creation needs to be atomic, and it is a single INSERT. The
    updates after it are single statements on a row nobody else holds.
    """
    organization_id = branch.organization_id
    till = till_for(organization_id)

    phone = normalise_phone(phone)
    shillings = whole_shillings(Decimal(amount))

    token = StkPush.new_token()
    with transaction.atomic():
        push = StkPush.objects.create(
            organization_id=organization_id,
            branch=branch,
            amount=Decimal(amount),
            phone_number=phone,
            callback_token_digest=token_digest(token),
            requested_by_staff=actor if _is_staff(actor) else None,
        )

    callback_url = f"{callback_base.rstrip('/')}/pay/mpesa/callback/{token}"

    try:
        answer = daraja.stk_push(
            till,
            amount=shillings,
            phone=phone,
            callback_url=callback_url,
            reference=till.account_reference or till.short_code,
            description=description,
        )
    except NotConfigured as error:
        raise PaymentError({"detail": str(error)}) from None
    except daraja.DarajaError as error:
        push.status = StkPush.Status.FAILED
        push.result_description = str(error)[:255]
        push.settled_at = timezone.now()
        push.save(update_fields=["status", "result_description", "settled_at",
                                 "updated_at"])
        raise PaymentError({"detail": str(error)}) from None

    push.merchant_request_id = str(answer.get("MerchantRequestID", ""))[:64]
    push.checkout_request_id = str(answer.get("CheckoutRequestID", ""))[:64]
    push.save(update_fields=["merchant_request_id", "checkout_request_id",
                             "updated_at"])
    return push


def _is_staff(actor) -> bool:
    from organisations.models import OrganizationStaff

    return isinstance(actor, OrganizationStaff)


def note_callback(token: str) -> StkPush | None:
    """
    A callback arrived. Record that, and nothing else.

    ── IT DOES NOT READ THE BODY, ON PURPOSE ───────────────────────────────
    The endpoint is public and anybody can post to it. Nothing Safaricom
    claims in a request we did not authenticate is allowed to decide whether
    a shop has been paid — the body is not parsed, not stored and not
    believed. All a valid token does is tell us which push to go and ask
    about.
    """
    push = StkPush.objects.filter(callback_token_digest=token_digest(token)).first()
    if push is None:
        return None
    if not push.callback_seen_at:
        push.callback_seen_at = timezone.now()
        push.save(update_fields=["callback_seen_at", "updated_at"])
    return push


@transaction.atomic
def confirm(push: StkPush) -> StkPush:
    """
    Ask Safaricom what happened, and settle the push on their answer.

    Idempotent and safe to call from a poll loop: a settled push is returned
    untouched rather than queried again.
    """
    push = StkPush.objects.select_for_update().get(pk=push.pk)
    if push.is_settled:
        return push

    if not push.checkout_request_id:
        # The push never reached Safaricom, so there is nothing to ask about.
        return _settle(push, StkPush.Status.FAILED, "", "", "Never sent")

    age = (timezone.now() - push.created_at).total_seconds()
    till = MpesaTill.objects.get(organization_id=push.organization_id)

    try:
        answer = daraja.stk_query(
            till, checkout_request_id=push.checkout_request_id
        )
    except (daraja.DarajaError, NotConfigured):
        # Unreachable is not "unpaid". Leave it waiting and let the next poll
        # ask again — marking a push failed because OUR network blipped would
        # tell a cashier to take cash from a customer who has already paid.
        if age > EXPIRE_AFTER_SECONDS:
            return _settle(push, StkPush.Status.EXPIRED, "", "",
                           "No answer from M-Pesa in time")
        return push

    code = str(answer.get("ResultCode", ""))
    description = str(answer.get("ResultDesc", ""))[:255]

    if code in PENDING_CODES:
        if age > EXPIRE_AFTER_SECONDS:
            return _settle(push, StkPush.Status.EXPIRED, code, "",
                           "The customer did not respond in time")
        return push

    if code == "0":
        # The query reports success but carries no receipt number; the
        # callback does. Taking the receipt from the callback would mean
        # trusting a public endpoint for a value printed on the customer's
        # receipt — so it is left blank rather than guessed, and the cashier
        # can read the code off the customer's SMS if they need it.
        return _settle(push, StkPush.Status.PAID, code,
                       str(answer.get("MpesaReceiptNumber", ""))[:32],
                       description or "Paid")

    return _settle(push, StkPush.Status.FAILED, code, "",
                   description or "Not paid")


def _settle(push, status, code, receipt, description) -> StkPush:
    push.status = status
    push.result_code = code[:8]
    push.mpesa_receipt = receipt
    push.result_description = description[:255]
    push.settled_at = timezone.now()
    push.save(update_fields=["status", "result_code", "mpesa_receipt",
                             "result_description", "settled_at", "updated_at"])

    # Every outcome passes through here, which is why the notification hangs
    # off this and not off the three callers that decide what the outcome is.
    # It cannot fail the settlement — see notifications.services._safely.
    notifications.payment_settled(push=push)

    return push


@transaction.atomic
def spend(push: StkPush, sale) -> StkPush:
    """
    Attach a confirmed payment to the sale it paid for.

    ── A PAYMENT IS SPENT ONCE ─────────────────────────────────────────────
    `StkPush.sale` is a OneToOne and the database enforces it, but the check
    is here too so the refusal is a sentence rather than an IntegrityError:
    at a busy till the way this gets attempted is a double-tap, not fraud.
    """
    push = StkPush.objects.select_for_update().get(pk=push.pk)
    if push.status != StkPush.Status.PAID:
        raise PaymentError({"detail": "That payment has not been confirmed."})
    if push.sale_id is not None:
        raise PaymentError(
            {"detail": "That payment has already paid for another sale."}
        )
    if push.organization_id != sale.organization_id:
        # Belt and braces over the scoping layer. A payment crossing tenants
        # is not a thing that should need a second check, and that is exactly
        # why it has one.
        raise PaymentError({"detail": "That payment belongs to another business."})
    if push.amount != sale.total:
        raise PaymentError(
            {
                "detail": (
                    f"The payment was {push.amount} and the sale comes to "
                    f"{sale.total}. They have to match."
                )
            }
        )

    push.sale = sale
    push.save(update_fields=["sale", "updated_at"])
    return push
