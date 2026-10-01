"""
Talking to Safaricom's Daraja API.

═══════════════════════════════════════════════════════════════════════════════
WHAT THIS MUST NEVER DO.

**Never log a consumer secret, a passkey, or the generated password.** The
password is base64(shortcode + passkey + timestamp) and is therefore the
passkey in a thin disguise — a log line carrying one has leaked the other.
`gen-portal/portal/mpesa.py` carries the same prohibition and it is in
CLAUDE.md for both.

Errors here log the HTTP status, Safaricom's own error text and the shortcode.
Never the request body, because the request body contains the password.
═══════════════════════════════════════════════════════════════════════════════

── NO NEW DEPENDENCY FOR THE HTTP ──────────────────────────────────────────────

Charter 03 §I. This is three JSON requests with a bearer token; urllib has done
that since Python 2. `cryptography` was added for storing the credentials
because the standard library genuinely cannot encrypt — see `crypto.py` — and
that is the whole of the exception. It does not extend to an HTTP client.

── THE QUERY IS THE ONE THAT MATTERS ───────────────────────────────────────────

`stk_push` starts something. `stk_query` is what the application believes. See
the banner in `models.py`: the callback URL is public and a callback decides
nothing.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger(__name__)

SANDBOX = "https://sandbox.safaricom.co.ke"
PRODUCTION = "https://api.safaricom.co.ke"

TIMEOUT = 30


class DarajaError(RuntimeError):
    """Safaricom refused, or could not be reached. Carries nothing secret."""

    def __init__(self, message: str, *, status: int | None = None, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


def base_url(environment: str) -> str:
    return PRODUCTION if environment == "production" else SANDBOX


def _request(url: str, *, method: str = "GET", headers: dict, body=None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read().decode()[:400]
        except Exception:  # noqa: BLE001 — diagnosis only
            pass
        # The URL and the status, never the body we sent.
        log.error("daraja refused: HTTP %s %s %s", error.code, url, detail)
        raise DarajaError(
            "M-Pesa refused the request.", status=error.code
        ) from None
    except urllib.error.URLError as error:
        log.error("daraja unreachable: %s", error.reason)
        raise DarajaError("M-Pesa could not be reached.") from None
    except json.JSONDecodeError:
        log.error("daraja returned something that was not JSON from %s", url)
        raise DarajaError("M-Pesa returned an unreadable response.") from None


def access_token(till) -> str:
    """
    A bearer token, good for an hour.

    Not cached. A till rings up a handful of M-Pesa payments an hour, the
    token call is one round trip, and a cache keyed per tenant is a place for
    one shop's token to be handed to another. Revisit if a busy chain makes
    it measurable; it is a thinking cost, not a performance one, today.
    """
    secrets_ = till.credentials()
    pair = f"{secrets_['consumer_key']}:{secrets_['consumer_secret']}".encode()
    payload = _request(
        f"{base_url(till.environment)}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": "Basic " + base64.b64encode(pair).decode()},
    )
    token = payload.get("access_token")
    if not token:
        raise DarajaError("M-Pesa did not return an access token.")
    return token


def _password(till, stamp: str) -> str:
    """base64(shortcode + passkey + timestamp). NEVER log the result."""
    passkey = till.credentials()["passkey"]
    return base64.b64encode(f"{till.short_code}{passkey}{stamp}".encode()).decode()


def stk_push(till, *, amount: int, phone: str, callback_url: str,
             reference: str, description: str) -> dict:
    """
    Ask the customer's phone to approve a payment.

    `amount` is a whole number of shillings — M-Pesa cannot move cents, and
    `services.py` refuses a sale total that is not whole rather than rounding
    somebody's money in either direction.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    body = {
        "BusinessShortCode": till.short_code,
        "Password": _password(till, stamp),
        "Timestamp": stamp,
        "TransactionType": till.transaction_type,
        "Amount": amount,
        "PartyA": phone,
        "PartyB": till.short_code,
        "PhoneNumber": phone,
        "CallBackURL": callback_url,
        "AccountReference": reference[:12] or till.short_code,
        "TransactionDesc": description[:13] or "Payment",
    }
    return _request(
        f"{base_url(till.environment)}/mpesa/stkpush/v1/processrequest",
        method="POST",
        headers={"Authorization": f"Bearer {access_token(till)}"},
        body=body,
    )


def stk_query(till, *, checkout_request_id: str) -> dict:
    """
    Ask Safaricom what actually happened. This is the answer we believe.

    ── "STILL PROCESSING" IS NOT A FAILURE ─────────────────────────────────
    While the customer is staring at the PIN prompt, Daraja answers with
    errorCode 500.001.1001 ("transaction is being processed"). Treating that
    as a refusal would mark a push failed seconds before the customer pays,
    so the caller distinguishes it — see `services.confirm`.
    """
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    body = {
        "BusinessShortCode": till.short_code,
        "Password": _password(till, stamp),
        "Timestamp": stamp,
        "CheckoutRequestID": checkout_request_id,
    }
    try:
        return _request(
            f"{base_url(till.environment)}/mpesa/stkpushquery/v1/query",
            method="POST",
            headers={"Authorization": f"Bearer {access_token(till)}"},
            body=body,
        )
    except DarajaError as error:
        # Daraja answers an in-flight query with HTTP 500 and a body saying
        # it is processing. Surfacing that as an outage would make the till
        # show an error for the entire time a customer is typing their PIN.
        if error.status == 500:
            return {"ResultCode": "1032-pending", "ResultDesc": "Still processing"}
        raise
