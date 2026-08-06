from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.workspace.services.port_access import (
    PortAccessInvalid,
    PortAccessSigner,
    PortGrant,
)

KEY = b"k" * 32


def _grant(**overrides) -> PortGrant:
    defaults = dict(
        sandbox_id=uuid4(),
        port=4848,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    defaults.update(overrides)
    return PortGrant(**defaults)  # type: ignore[arg-type]


def test_a_signed_grant_round_trips() -> None:
    signer = PortAccessSigner(key=KEY)
    grant = _grant()
    verified = signer.verify(signer.sign(grant))

    assert verified.sandbox_id == grant.sandbox_id
    assert verified.port == grant.port
    assert int(verified.expires_at.timestamp()) == int(grant.expires_at.timestamp())


def test_the_signature_covers_the_sandbox_so_a_port_cannot_be_repointed() -> None:
    """Otherwise a grant for your own workspace would open anyone else's."""
    signer = PortAccessSigner(key=KEY)
    mine = signer.sign(_grant(sandbox_id=uuid4()))
    payload, _, signature = mine.partition(".")

    forged_payload = PortAccessSigner(key=KEY).sign(_grant(sandbox_id=uuid4())).split(".")[0]
    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{forged_payload}.{signature}")
    del payload


def test_the_signature_covers_the_port() -> None:
    signer = PortAccessSigner(key=KEY)
    sandbox_id = uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    browser = signer.sign(_grant(sandbox_id=sandbox_id, port=4848, expires_at=expires))
    runtime_payload = signer.sign(
        _grant(sandbox_id=sandbox_id, port=8080, expires_at=expires)
    ).split(".")[0]

    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{runtime_payload}.{browser.split('.')[1]}")


def test_an_expired_grant_is_refused() -> None:
    """Revocation is the clock, so the expiry has to be enforced on read."""
    signer = PortAccessSigner(key=KEY)
    token = signer.sign(
        _grant(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    with pytest.raises(PortAccessInvalid, match="expired"):
        signer.verify(token)


def test_a_grant_signed_with_another_key_is_refused() -> None:
    token = PortAccessSigner(key=b"a" * 32).sign(_grant())
    with pytest.raises(PortAccessInvalid):
        PortAccessSigner(key=b"b" * 32).verify(token)


@pytest.mark.parametrize("token", ["", "nodot", ".", "abc.", ".abc", "a.b.c"])
def test_malformed_tokens_are_refused_rather_than_crashing(token: str) -> None:
    with pytest.raises(PortAccessInvalid):
        PortAccessSigner(key=KEY).verify(token)


def test_a_re_encoded_payload_does_not_verify() -> None:
    """The MAC covers the exact bytes presented, so an equivalent-but-different
    base64 encoding of the same claims must not be accepted."""
    signer = PortAccessSigner(key=KEY)
    payload, _, signature = signer.sign(_grant()).partition(".")
    with pytest.raises(PortAccessInvalid):
        signer.verify(f"{payload}=.{signature}")


def test_a_short_key_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        PortAccessSigner(key=b"tooshort")
