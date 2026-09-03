"""How long a started connection may take to come back, and what is left after.

Expiry and single use are both decided inside `claim_pending_by_state`, in one
UPDATE, so they are exercised against the repository rather than here -- see
`test_accounts_connect_requests_e2e`. What is left is the constant the claim is
built from, and the scrub that runs once the exchange has happened.

There was a second, unreached implementation of the same rule with the same
`created_at` arithmetic, and these tests covered that one. A dead rule with
passing tests is worse than no tests: it reports green on something production
does not call, and reads to the next person as the live decision.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.services.connect_request_lifecycle import (
    CONNECT_REQUEST_TTL,
    oldest_claimable_connect_request,
    without_spent_secrets,
)


def _request(*, age: timedelta, status=ConnectRequestStatus.PENDING, aware=True):
    started = datetime.now(timezone.utc) - age
    return ConnectRequestEntity(
        id=uuid4(),
        user_id=uuid4(),
        organization_id=uuid4(),
        auth_config_id=uuid4(),
        connector_id="slack",
        status=status,
        created_at=started if aware else started.replace(tzinfo=None),
        attributes={"state": "s", "code_verifier": "v", "provider_state": "ca_1"},
    )


def test_the_claim_window_matches_the_ttl():
    """The age test moved into the claiming UPDATE, so the two must agree --
    otherwise expiry is enforced in one place and not the other."""
    cutoff = oldest_claimable_connect_request()
    slack = abs((datetime.now(timezone.utc) - CONNECT_REQUEST_TTL) - cutoff)
    assert slack < timedelta(seconds=5)


def test_both_spent_secrets_are_cleared():
    """`provider_state` is the Composio connection id — the capability the
    callback binding exists to keep from an attacker. It protects nothing once
    the exchange is done and identifies something for as long as the row
    lives, which is forever."""
    remaining = without_spent_secrets(_request(age=timedelta(minutes=1)))

    assert remaining == {"state": "s"}


def test_nothing_to_clear_is_not_an_error():
    entity = _request(age=timedelta(minutes=1))
    entity.attributes = None
    assert without_spent_secrets(entity) is None
