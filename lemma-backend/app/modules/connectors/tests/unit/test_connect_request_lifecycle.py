"""How long a started connection may take to come back, and what is left after.

The single-use half of this had a test. The expiry half had none, and neither
did the naive-datetime normalisation underneath it -- a row read back without a
timezone would raise on the comparison rather than expire, turning a stale
request into a 500.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.connectors.domain.connect_request import (
    ConnectRequestEntity,
    ConnectRequestStatus,
)
from app.modules.connectors.domain.errors import ConnectRequestNotFoundError
from app.modules.connectors.services.connect_request_lifecycle import (
    CONNECT_REQUEST_TTL,
    assert_still_open,
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


def test_a_fresh_request_is_open():
    assert_still_open(_request(age=timedelta(minutes=1)))


def test_a_request_older_than_the_ttl_is_refused():
    """A leaked `state` stops being a capability after the window closes."""
    with pytest.raises(ConnectRequestNotFoundError):
        assert_still_open(_request(age=CONNECT_REQUEST_TTL + timedelta(minutes=1)))


def test_a_naive_created_at_is_read_as_utc_rather_than_raising():
    """The row can come back without a timezone. Comparing a naive datetime to
    an aware one raises `TypeError`, which would surface as a 500 on a stale
    callback instead of a refusal."""
    with pytest.raises(ConnectRequestNotFoundError):
        assert_still_open(
            _request(age=CONNECT_REQUEST_TTL + timedelta(minutes=1), aware=False)
        )
    assert_still_open(_request(age=timedelta(minutes=1), aware=False))


@pytest.mark.parametrize(
    "status", [ConnectRequestStatus.SUCCESS, ConnectRequestStatus.ERROR]
)
def test_a_finished_request_is_refused_however_recent(status):
    with pytest.raises(ConnectRequestNotFoundError):
        assert_still_open(_request(age=timedelta(seconds=1), status=status))


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
