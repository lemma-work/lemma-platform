"""What a connect request is still allowed to do, and what it remembers.

A connect request is the only thing that survives from the moment somebody is
sent to a provider until the moment they come back, so it carries the two
secrets that have to outlive the redirect -- the PKCE verifier and whatever the
provider calls this authorization. That makes its `state` a capability, and the
rules for spending one live here rather than inline in the service, where they
were absent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.errors import ConnectRequestNotFoundError

# Long enough for a person to read a consent screen, find a password manager
# and pass an MFA prompt; short enough that a leaked `state` is not a standing
# capability.
CONNECT_REQUEST_TTL = timedelta(minutes=30)


def assert_still_open(connect_request: ConnectRequestEntity) -> None:
    """Refuse a callback for a flow that is finished, failed, or stale.

    The status used to be written on every path and read on none, so a `state`
    was a permanent bearer capability meaning "attach a connector account to
    this user in this org". It travels through the provider's redirect, so it
    lands in browser history, proxy logs and Referer headers -- and replaying
    it with a fresh authorization code obtained for the same client stores the
    replayer's provider identity as an account belonging to whoever started the
    flow. Their agents and schedules then act through it.

    Single use and time-bounded together: expiry alone still allows a replay
    inside the window, and single use alone leaves an abandoned request valid
    forever.

    Raises the not-found error rather than anything more specific, because a
    caller holding a `state` should not learn whether it was wrong, spent or
    merely old.
    """
    if connect_request.status is not ConnectRequestStatus.PENDING:
        raise ConnectRequestNotFoundError()
    started = connect_request.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - started > CONNECT_REQUEST_TTL:
        raise ConnectRequestNotFoundError()


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

    `attributes` is plaintext JSONB on a row nothing ever deletes, so a spent
    verifier left in it is a readable secret with nothing left to protect.
    """
    if not connect_request.attributes:
        return connect_request.attributes
    return {
        key: value
        for key, value in connect_request.attributes.items()
        if key != "code_verifier"
    }
