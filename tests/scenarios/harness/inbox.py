"""Reading a real inbox back, for the one thing provisioning cannot sign around.

Every other gate a real deployment keeps in front of registration is something
the standing cast avoids by signing in rather than up (see `tenant.py`). Email
verification is not avoidable that way — the token only ever exists inside the
email the deployment actually sent, so the only honest way through the gate is
to receive that email and use the token in it, the way a person would.

This reads Resend's inbound API rather than running a webhook receiver: a
one-shot provisioning script has no server for Resend to call, and polling a
handful of times is simpler than standing one up for five emails, once.

Configured the same way `credentials.py` configures the live lane — a setting
this run either has or does not, never a secret with a default:

    SCENARIOS_RESEND_API_KEY   a Resend key with read access to the inbound
                                domain the standing cast's addresses resolve to
                                (see tenant.py's SCENARIOS_TENANT_DOMAIN)
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime

import httpx

API_KEY_SETTING = "SCENARIOS_RESEND_API_KEY"

#: The link in the email points at the frontend: .../auth/verify-email?token=…
#: A person clicks it; this reads the same query parameter out of the message
#: instead, since the token is all `verifies_email` needs.
_TOKEN_IN_LINK = re.compile(r"[?&]token=([^&\"'\s<]+)")

#: How long to wait for one email, and how often to ask. A deployment sending
#: through a real provider takes a few seconds, not milliseconds — polling
#: faster than that would only spend more of Resend's own rate limit for no
#: extra timeliness.
_TIMEOUT_SECONDS = 90.0
_POLL_INTERVAL_SECONDS = 3.0


class InboxUnavailable(AssertionError):
    """Reading the inbox is not possible or did not produce the email."""


def configured() -> bool:
    return bool(os.getenv(API_KEY_SETTING, "").strip())


def _client() -> httpx.Client:
    key = os.getenv(API_KEY_SETTING, "").strip()
    if not key:
        raise InboxUnavailable(
            f"{API_KEY_SETTING} is not set. This deployment requires email "
            f"verification (AUTH_EMAIL_VERIFICATION_REQUIRED=true) and "
            f"provisioning cannot get past it without reading the standing "
            f"cast's real inbox — set {API_KEY_SETTING} to a Resend key that "
            f"can read mail sent to the standing cast's domain."
        )
    return httpx.Client(
        base_url="https://api.resend.com",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30.0,
    )


def wait_for_verification_link(to: str, *, since: float) -> str:
    """The verification token from the newest email to ``to`` sent after ``since``.

    ``since`` is a ``time.time()`` taken before the email was requested — not
    optional, because Resend keeps 30 days of received mail and the standing
    cast's addresses have almost certainly been sent to before. Reading a stale
    token off an old email would fail confusingly, much later, on the actual
    verify call, instead of here where the cause is obvious.
    """
    with _client() as client:
        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while True:
            response = client.get("/emails/receiving", params={"limit": 20})
            response.raise_for_status()
            candidates = [
                item
                for item in response.json().get("data", [])
                if to in item.get("to", []) and _after(item.get("created_at"), since)
            ]
            if candidates:
                newest = max(candidates, key=lambda item: item.get("created_at", ""))
                return _token_from(client, newest["id"], to=to)
            if time.monotonic() >= deadline:
                raise InboxUnavailable(
                    f"no verification email reached {to} within "
                    f"{_TIMEOUT_SECONDS:.0f}s. Confirm {API_KEY_SETTING} reads "
                    f"the domain {to.rsplit('@', 1)[-1]!r} resolves to, and "
                    f"that its MX record actually points at Resend."
                )
            time.sleep(_POLL_INTERVAL_SECONDS)


def _after(created_at: str | None, since: float) -> bool:
    if not created_at:
        return False
    try:
        # A 5s allowance for clock skew between here and Resend — not for
        # correctness of "after", but so a request timed right at the second
        # boundary is never the one email this rejects for being 200ms early.
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return parsed.timestamp() >= since - 5
    except ValueError:
        return True  # an unparsable timestamp should not hide a real email


def _token_from(client: httpx.Client, email_id: str, *, to: str) -> str:
    response = client.get(f"/emails/receiving/{email_id}")
    response.raise_for_status()
    full = response.json()
    haystacks = (full.get("html") or "", full.get("text") or "")
    for haystack in haystacks:
        match = _TOKEN_IN_LINK.search(haystack)
        if match:
            return match.group(1)
    raise InboxUnavailable(
        f"an email reached {to} (subject {full.get('subject')!r}) but no "
        f"verification link was in it. Either the template changed — update "
        f"_TOKEN_IN_LINK in harness/inbox.py — or this was not the "
        f"verification email at all."
    )
