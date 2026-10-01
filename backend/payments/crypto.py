"""
Encrypting other companies' merchant credentials at rest.

═══════════════════════════════════════════════════════════════════════════════
WHOSE SECRETS THESE ARE.

A Daraja consumer key, secret and passkey belong to the CUSTOMER, not to
Genmars. Holding them makes this database a place worth breaking into for a
reason that has nothing to do with the shop's own data: whoever takes them can
initiate payment requests against that business's M-Pesa account.

That is why they are encrypted rather than merely access-controlled, and why
`subscriptions/services.py` goes to some length to keep GENMARS' own M-Pesa
credentials out of this application entirely — those are brokered by
gen-portal, which is not multi-tenant and does not host anybody else's data.
═══════════════════════════════════════════════════════════════════════════════

── CHARTER 03 §I: WHY A DEPENDENCY ENTERS HERE ─────────────────────────────────

"A dependency enters the stack only when what is already there cannot do the
job." The standard library cannot do this one. `hashlib` and `hmac` authenticate
and `secrets` generates, but nothing in Python ships symmetric encryption, and
hand-rolling AES-GCM over a KDF is precisely the code that should not be
hand-rolled. `cryptography` is PyCA's, it is what Django's own docs point at,
and the alternative is other businesses' merchant secrets sitting in plaintext
in a Postgres dump.

── THE KEY IS ITS OWN, NOT DJANGO'S SECRET_KEY ─────────────────────────────────

`MPESA_CREDENTIAL_KEY`, read from the environment, and deliberately NOT derived
from `DJANGO_SECRET_KEY`.

Rotating `SECRET_KEY` is an ordinary thing to do — it is what you do the moment
you think it may have leaked, and the documented consequence is that sessions
and signed cookies are invalidated. If merchant credentials hung off it,
rotation would silently turn every tenant's M-Pesa configuration into
undecryptable noise, and the shop would find out at the till. One key, one
blast radius.

── ABSENT MEANS OFF, NOT INSECURE ──────────────────────────────────────────────

With no key configured this module refuses to encrypt or decrypt and the
configuration endpoints say so. It does NOT fall back to storing plaintext, and
it does NOT generate an ephemeral key — an ephemeral key looks like it works
until the process restarts, at which point every stored credential is rubbish
and nothing says why. Same shape as `RESEND_API_KEY`: present and it works,
absent and the feature is honestly unavailable.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class NotConfigured(RuntimeError):
    """No MPESA_CREDENTIAL_KEY. The feature is off; say so, do not improvise."""


def _fernet():
    from cryptography.fernet import Fernet

    key = getattr(settings, "MPESA_CREDENTIAL_KEY", "") or ""
    if not key:
        raise NotConfigured(
            "MPESA_CREDENTIAL_KEY is not set, so M-Pesa credentials cannot be "
            "stored or read. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and put it in the "
            "environment."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as error:  # noqa: BLE001 — the library raises several
        raise ImproperlyConfigured(
            "MPESA_CREDENTIAL_KEY is not a valid Fernet key (32 url-safe "
            "base64-encoded bytes)."
        ) from error


def seal(value: str) -> str:
    """
    Plaintext in, ciphertext out.

    An empty value seals to an empty string rather than to ciphertext, so
    "this field was never filled in" stays distinguishable from "this field
    holds an empty secret" without a second column.
    """
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def unseal(value: str) -> str:
    """
    Ciphertext in, plaintext out.

    ⚠ EVERY CALLER OF THIS IS A PLACE A SECRET CAN ESCAPE. There are three in
    the application and all three are in `payments/daraja.py`, where the value
    goes straight into an HTTPS request body and is never held, logged or
    returned.
    """
    if not value:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        # Almost always the key having changed under stored data. Say that,
        # rather than letting a library exception surface as a 500 that reads
        # like a bug in the till.
        raise NotConfigured(
            "Stored M-Pesa credentials cannot be decrypted with the current "
            "MPESA_CREDENTIAL_KEY. They were sealed with a different one and "
            "have to be entered again."
        ) from error


def is_configured() -> bool:
    """Whether the platform can hold credentials at all. Not whether a tenant has."""
    return bool(getattr(settings, "MPESA_CREDENTIAL_KEY", ""))
