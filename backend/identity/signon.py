"""
The Genmars sign-on client — the subscriber half of the door.

═══════════════════════════════════════════════════════════════════════════════
THE PROTOCOL IS gen-portal's, AND THE CONTRACT IS docs/SIBLING-SIGN-ON.md.

    1. send the person to  app.genmars.co.ke/sign-on?client_id=&redirect_uri=&state=
    2. they come back to   /auth/callback?code=&state=
    3. THIS SERVER swaps the code at  api.genmars.co.ke/api/auth/sign-on/token

Three properties of that contract are load-bearing and are enforced here:

  · `state` is OURS. The portal echoes it back untouched and decides nothing
    from it. Generating it per attempt and comparing on return is the CSRF
    defence for this flow, and nothing on the portal's side substitutes for it.
  · The code lives **90 seconds** and is single-use. A presented-but-wrong code
    is burned at the other end.
  · Step 3 is made from the SERVER. The client secret never reaches a browser.
═══════════════════════════════════════════════════════════════════════════════

── urllib, NOT requests ────────────────────────────────────────────────────────

One POST of one JSON object to one known host. `requests` is not in
requirements.txt and Charter 03 §I admits a dependency only when what is here
cannot do the job; the standard library can do this one.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlencode

from django.conf import settings

STATE_SESSION_KEY = "genmars_sign_on_state"
NEXT_SESSION_KEY = "genmars_sign_on_next"

# Every refusal reads the same, exactly as the portal's own /token does. A
# message that distinguishes "expired" from "already used" from "wrong
# application" tells somebody holding a stolen code which part to fix.
SIGN_ON_FAILED = "That sign-in could not be completed. Start again."


class SignOnError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.safe_message = SIGN_ON_FAILED


def _config(name: str) -> str:
    value = getattr(settings, name, "")
    if not value:
        raise SignOnError(f"missing_setting:{name}")
    return value


def start_url(request) -> str:
    """
    Where to send the person, and the state that must come back with them.

    The state is stored in THIS session. A callback carrying a state that does
    not match the session it arrives in is not a sign-in, it is somebody else's
    link being clicked.
    """
    state = secrets.token_urlsafe(24)
    request.session[STATE_SESSION_KEY] = state

    query = urlencode(
        {
            "client_id": _config("GENMARS_SIGN_ON_CLIENT_ID"),
            "redirect_uri": _config("GENMARS_SIGN_ON_REDIRECT_URI"),
            "state": state,
        }
    )
    return f"{_config('GENMARS_PORTAL_ORIGIN')}/sign-on?{query}"


def check_state(request, returned: str) -> None:
    """
    Compare and then DISCARD, so a state cannot be replayed.

    Popped whether or not it matches: leaving a used state in the session turns
    one intercepted callback into an unlimited number of them.
    """
    expected = request.session.pop(STATE_SESSION_KEY, None)
    if not expected or not returned or not secrets.compare_digest(expected, returned):
        raise SignOnError("state_mismatch")


def exchange_code(code: str) -> dict:
    """
    Swap the authorization code for the account, from this server.

    Fails closed on everything: a non-200, a body that is not JSON, a timeout, a
    refused connection. There is no partial success worth salvaging when the
    question is "who is this".
    """
    body = json.dumps(
        {
            "client_id": _config("GENMARS_SIGN_ON_CLIENT_ID"),
            "client_secret": _config("GENMARS_SIGN_ON_CLIENT_SECRET"),
            "code": code,
            "redirect_uri": _config("GENMARS_SIGN_ON_REDIRECT_URI"),
        }
    ).encode()

    request_ = urllib.request.Request(
        f"{_config('GENMARS_API_ORIGIN')}/api/auth/sign-on/token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        # The code expires in 90 seconds, so a long timeout buys nothing but a
        # user staring at a spinner while the thing they are waiting for dies.
        with urllib.request.urlopen(request_, timeout=10) as response:
            if response.status != 200:
                raise SignOnError(f"token_status:{response.status}")
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise SignOnError(f"token_http:{error.code}") from None
    except urllib.error.URLError as error:
        raise SignOnError(f"token_unreachable:{error.reason}") from None
    except (ValueError, TimeoutError) as error:
        raise SignOnError(f"token_unreadable:{type(error).__name__}") from None
