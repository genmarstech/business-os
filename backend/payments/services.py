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

# ══════════════════════════════════════════════════════════════════════════
# WHICH ANSWERS END A PUSH, AND WHY EVERYTHING ELSE DOES NOT.
#
# ⚠ THIS LIST IS THE ONLY THING ALLOWED TO TURN A PUSH INTO "NOT PAID".
#   Adding a default branch that fails on an unrecognised code re-creates
#   the bug below. Do not.
#
# This used to be the other way round: a small set of PENDING_CODES was
# treated as "still going" and EVERY OTHER CODE was treated as a refusal.
# Then production answered `4999 — The transaction is still under
# processing`, which is not in that set, and the push was marked failed
# TEN SECONDS after it was sent, while the customer was still looking at
# the PIN prompt. Worse, `confirm` returns a settled push untouched, so
# when the money did arrive nothing ever asked again: a real payment of
# KSh 80 sat in the database as a failure, funding no sale.
#
# So the default direction is reversed. An answer is final only if it is
# listed here; anything else means keep asking until EXPIRE_AFTER_SECONDS.
# The worst case of that is a cashier waiting three minutes before taking
# cash. The worst case of the old default is a customer paying twice.
#
# It is the same instinct as the DarajaError branch in `confirm`, which has
# said "unreachable is not unpaid" since this file was written. An
# unrecognised code is not unpaid either.
#
# The text is ours, not Safaricom's: `ResultDesc` is written for a
# developer ("Request Cancelled by user."), and this is read aloud at a
# counter with the customer listening.
FINAL_FAILURES = {
    "1": "There was not enough money in the M-Pesa account.",
    "17": "M-Pesa could not process that one.",
    "1001": "Another M-Pesa payment is already going through on that phone.",
    "1019": "The request ran out of time at M-Pesa.",
    "1025": "M-Pesa could not process that one.",
    "1032": "The customer cancelled it.",
    "1037": "The phone could not be reached.",
    "2001": "The PIN was wrong.",
    "9999": "M-Pesa could not process that one.",
}

# Kept for what it documents rather than for what it decides: these are the
# in-flight answers seen in the wild. `4999` is the one that cost real
# money. Nothing branches on membership of this set any more — a code is
# pending because it is NOT in FINAL_FAILURES, not because it is here.
PENDING_CODES = {"1032-pending", "500.001.1001", "4999"}

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


def till_for(branch) -> MpesaTill:
    """
    The M-Pesa number this branch is paid on, or a refusal in words.

    ── IT TAKES A BRANCH, NOT AN ORGANISATION ──────────────────────────────
    It used to take an organisation id, because there was one till per
    business. A branch can now carry its own — see `MpesaTill.for_branch`,
    which is the only thing that chooses between that and the default.

    Every refusal below names the BRANCH where a branch override is what is
    broken. "M-Pesa is not set up for this business" is actively misleading
    at a shop whose head office has had it working for months, and it sends
    an owner to the wrong settings screen.
    """
    till = MpesaTill.for_branch(branch)
    where = (
        f"this branch ({branch.branch_name})"
        if till is not None and till.branch_id
        else "this business"
    )
    if till is None:
        raise PaymentError(
            {"detail": "M-Pesa is not set up for this business yet. "
                       "An owner turns it on under Settings."}
        )
    if not till.is_active:
        raise PaymentError(
            {"detail": f"M-Pesa is switched off for {where}. "
                       "An owner turns it on under Settings."}
        )
    if not till.is_complete:
        raise PaymentError(
            {"detail": f"The M-Pesa settings for {where} are incomplete. "
                       "An owner finishes them under Settings."}
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
    till = till_for(branch)

    phone = normalise_phone(phone)
    shillings = whole_shillings(Decimal(amount))

    token = StkPush.new_token()
    with transaction.atomic():
        push = StkPush.objects.create(
            organization_id=organization_id,
            branch=branch,
            # Recorded, so `confirm` queries under the number this actually
            # went out on rather than whatever is configured by then. See
            # the banner on StkPush.till.
            till=till,
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
    # The till the push WENT OUT ON. Falling back to the lookup only for
    # rows written before that column existed — every one of those was sent
    # on the organisation's single till, so the lookup is right for them and
    # wrong for everything after.
    till = push.till or MpesaTill.objects.filter(
        organization_id=push.organization_id, branch__isnull=True
    ).first()
    if till is None:
        return push

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

    if code == "0":
        # The query reports success but carries no receipt number; the
        # callback does. Taking the receipt from the callback would mean
        # trusting a public endpoint for a value printed on the customer's
        # receipt — so it is left blank rather than guessed, and the cashier
        # can read the code off the customer's SMS if they need it.
        return _settle(push, StkPush.Status.PAID, code,
                       str(answer.get("MpesaReceiptNumber", ""))[:32],
                       description or "Paid")

    if code in FINAL_FAILURES:
        return _settle(push, StkPush.Status.FAILED, code, "",
                       FINAL_FAILURES[code])

    # ── NOT RECOGNISED MEANS NOT FINISHED ──────────────────────────────
    # See the banner on FINAL_FAILURES. `4999` arrives here, which is the
    # whole point: Safaricom are saying the transaction is still under
    # processing, and the only safe reading of an answer we do not know is
    # that it has not finished yet.
    if age > EXPIRE_AFTER_SECONDS:
        return _settle(push, StkPush.Status.EXPIRED, code, "",
                       "The customer did not respond in time")
    return push


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
def recheck(push: StkPush) -> StkPush:
    """
    Ask Safaricom again about a push that was closed without being paid.

    ── WHY A CLOSED PUSH HAS TO BE RE-ASKABLE AT ALL ───────────────────────
    `confirm` returns a settled push untouched, which is right for a poll
    loop and wrong as a final word: the first production run of this code
    closed a push as failed on an answer that said "still under processing",
    and when the money landed a minute later there was no way back. The
    customer had paid, the shop's record said otherwise, and correcting it
    meant editing a payment row by hand.

    This does NOT decide anything. It clears the settlement and calls
    `confirm`, so the answer still comes from Safaricom and from nowhere
    else — the same rule that makes the public callback a hint rather than a
    verdict. A push that really did fail simply settles as failed again.

    ⚠ A PAID PUSH IS NEVER REOPENED. Paid is the one answer that cannot
      become something else: reopening it would let a spent payment be
      detached from the sale it funded.
    """
    push = StkPush.objects.select_for_update().get(pk=push.pk)
    if push.status == StkPush.Status.PAID:
        return push
    if not push.checkout_request_id:
        # It never reached Safaricom, so there is nobody to ask. Settled as
        # failed is the truth, not a guess worth revisiting.
        return push

    push.status = StkPush.Status.REQUESTED
    push.settled_at = None
    push.save(update_fields=["status", "settled_at", "updated_at"])
    return confirm(push)


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
