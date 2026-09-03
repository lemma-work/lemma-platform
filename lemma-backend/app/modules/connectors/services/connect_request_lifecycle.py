"""What a connect request is still allowed to do, and what it remembers.

A connect request is the only thing that survives from the moment somebody is
sent to a provider until the moment they come back, so it carries the two
secrets that have to outlive the redirect -- the PKCE verifier and whatever the
provider calls this authorization. That makes its `state` a capability, and the
rules for spending one live here rather than inline in the service, where they
were absent.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.modules.connectors.domain.auth_install import ResolvedAuthInstall
from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
)

# Long enough for a person to read a consent screen, find a password manager
# and pass an MFA prompt; short enough that a leaked `state` is not a standing
# capability.
CONNECT_REQUEST_TTL = timedelta(minutes=30)

# Cleared once the exchange has happened; see `without_spent_secrets`.
_SPENT_KEYS = frozenset({"code_verifier", "provider_state"})


def oldest_claimable_connect_request() -> datetime:
    """The earliest `created_at` a callback may still spend.

    Passed into the claim so the age test runs in the same UPDATE as the status
    test, rather than being decided in Python beside it.

    Single use and time-bounded together: expiry alone still allows a replay
    inside the window, and single use alone leaves an abandoned request valid
    forever. Both belong in that one UPDATE, which is why the Python-side
    `assert_still_open` that used to state the same rule beside it is gone --
    two implementations of one security rule, only one of them reached, and
    the unreached one was the one with tests.

    The `state` travels through the provider's redirect, so it lands in browser
    history, proxy logs and Referer headers; replaying it with a fresh
    authorization code obtained for the same client would otherwise store the
    replayer's provider identity as an account belonging to whoever started the
    flow, and their agents and schedules would then act through it.
    """
    return datetime.now(timezone.utc) - CONNECT_REQUEST_TTL


def stored_code_verifier(connect_request: ConnectRequestEntity) -> str | None:
    """The verifier minted when the redirect was built, if there was one."""
    return (connect_request.attributes or {}).get("code_verifier")


def stored_provider_state(connect_request: ConnectRequestEntity) -> str | None:
    """What the provider called this authorization when we started it.

    For Composio it is the connection request's id, which is the only thing
    tying a callback to the flow that began it -- see the check in
    ``ComposioAuthProvider.exchange_code_for_credentials``.
    """
    return (connect_request.attributes or {}).get("provider_state")


def without_spent_secrets(connect_request: ConnectRequestEntity) -> dict | None:
    """The attributes to keep once the exchange has happened.

    `attributes` is plaintext JSONB on a row nothing ever deletes, so anything
    spent that is left in it is a readable secret with nothing left to protect.

    `provider_state` goes too, and it is the more important of the two. For
    Composio it is the connection id -- precisely the capability the callback
    binding exists to keep out of an attacker's hands, since anyone holding one
    could have had that connection's tokens stored onto their own account. Once
    the exchange is done it protects nothing and identifies something.
    """
    if not connect_request.attributes:
        return connect_request.attributes
    return {
        key: value
        for key, value in connect_request.attributes.items()
        if key not in _SPENT_KEYS
    }


def pkce_verifier_for(install: ResolvedAuthInstall) -> str | None:
    """A PKCE verifier for every OAuth connect, secret or no secret.

    Minted out here rather than inside the provider because it has to outlive
    the request that makes it: the callback is where it is needed, and the
    connect request is the thing that lives that long.

    This used to be reserved for secretless clients, on the reasoning that a
    client with a secret already proves itself. That confuses two different
    questions. The secret proves *which application* is exchanging the code; it
    says nothing about *which flow* the code came from. So a leaked `state` --
    which by construction travels through the provider's redirect into browser
    history, proxy logs and Referer headers -- plus a code the attacker minted
    for their own identity against the same deployment client was enough to
    store their account onto the victim's user. Making the request single-use
    bounds that to one attempt inside thirty minutes; it does not stop it.

    PKCE does, because the code is bound to the verifier that started the flow
    and the attacker's code carries a different one. RFC 7636 is explicit that
    a server which does not support the parameters ignores them, and every
    provider this connects to today accepts a challenge alongside a secret.
    """
    if install.oauth2 is None:
        return None
    return secrets.token_urlsafe(64)
