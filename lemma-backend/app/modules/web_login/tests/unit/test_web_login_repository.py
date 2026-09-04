from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.crypto import get_secret_cipher
from app.modules.web_login.domain.entities import (
    WebLogin,
    WebLoginKind,
    WebLoginSecret,
)
from app.modules.web_login.infrastructure.repository import (
    WebLoginNotFound,
    WebLoginRepository,
    _secret_to_json,
    _to_entity,
)


class _FakeSession:
    """Captures what would be written, without a database.

    The property under test is what leaves the process, so the interesting
    boundary is the row handed to the session — not what Postgres does with it.
    """

    def __init__(self, existing=None):
        self.added: list[object] = []
        self._existing = existing

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def delete(self, row):
        return None

    async def execute(self, statement):
        existing = self._existing

        class _Result:
            def scalar_one_or_none(self):
                return existing

            def scalars(self):
                return iter([existing] if existing is not None else [])

        return _Result()


PASSWORD = "correct-horse-battery-staple"
STATE = '{"cookies":[{"name":"sid","value":"s3cr3t-session-value"}]}'


@pytest.mark.asyncio
async def test_the_secret_is_encrypted_before_it_reaches_the_database() -> None:
    """The canary: neither the password nor the session may appear in the row."""
    session = _FakeSession()
    repository = WebLoginRepository(session, get_secret_cipher())

    await repository.save(
        user_id=uuid4(),
        origin="https://app.example.com",
        label="Example",
        kind=WebLoginKind.CREDENTIAL,
        secret=WebLoginSecret(
            state=STATE, username="a@b.test", password=PASSWORD, totp_seed="JBSWY3DP"
        ),
    )

    stored = repr(session.added[0].secret)
    assert PASSWORD not in stored
    assert "s3cr3t-session-value" not in stored
    assert "JBSWY3DP" not in stored
    # And it is the platform's envelope, so it rotates with everything else.
    assert session.added[0].secret.get("_encrypted")


@pytest.mark.asyncio
async def test_what_was_encrypted_can_be_read_back() -> None:
    cipher = get_secret_cipher()
    payload = _secret_to_json(
        WebLoginSecret(state=STATE, username="a@b.test", password=PASSWORD)
    )
    round_tripped = await cipher.decrypt_json_async(
        await cipher.encrypt_json_async(payload)
    )
    assert round_tripped["password"] == PASSWORD
    assert round_tripped["state"] == STATE


def test_the_listed_shape_has_nowhere_to_put_a_secret() -> None:
    """A type that cannot carry the secret cannot leak it by accident."""
    assert "secret" not in WebLogin.__annotations__
    assert "password" not in WebLogin.__annotations__
    assert "state" not in WebLogin.__annotations__


def test_an_entity_carries_only_what_is_safe_to_show() -> None:
    class _Row:
        id = uuid4()
        user_id = uuid4()
        origin = "https://app.example.com"
        label = "Example"
        kind = "SESSION"
        created_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        updated_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        last_used_at = None
        expires_hint_at = None

    entity = _to_entity(_Row())
    assert entity.kind is WebLoginKind.SESSION
    assert entity.has_password is False
    assert "secret" not in repr(entity)


@pytest.mark.asyncio
async def test_saving_the_same_origin_twice_replaces_rather_than_adds() -> None:
    """An agent handed two sessions for one site has no way to choose."""

    class _Row:
        id = uuid4()
        user_id = uuid4()
        origin = "https://app.example.com"
        label = "old"
        kind = "SESSION"
        secret = {"_encrypted": "x"}
        created_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        updated_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        last_used_at = None
        expires_hint_at = None

    row = _Row()
    session = _FakeSession(existing=row)
    repository = WebLoginRepository(session, get_secret_cipher())

    await repository.save(
        user_id=row.user_id,
        origin=row.origin,
        label="new",
        kind=WebLoginKind.SESSION,
        secret=WebLoginSecret(state=STATE),
    )

    assert session.added == []
    assert row.label == "new"


@pytest.mark.asyncio
async def test_the_audit_row_has_nowhere_to_put_a_secret() -> None:
    session = _FakeSession()
    repository = WebLoginRepository(session, get_secret_cipher())

    await repository.record(
        user_id=uuid4(),
        origin="https://app.example.com",
        action="inject",
        outcome="ok",
        actor="agent:researcher",
        detail="x" * 900,
    )

    row = session.added[0]
    assert not hasattr(row, "secret")
    # Detail is bounded, so a page's error text cannot become the audit trail.
    assert len(row.detail) == 500


@pytest.mark.asyncio
async def test_deleting_a_login_that_is_not_there_says_so() -> None:
    repository = WebLoginRepository(_FakeSession(), get_secret_cipher())
    with pytest.raises(WebLoginNotFound):
        await repository.delete(uuid4(), "https://never-saved.test")


@pytest.mark.asyncio
async def test_revealing_a_secret_that_is_not_there_returns_nothing() -> None:
    repository = WebLoginRepository(_FakeSession(), get_secret_cipher())
    assert await repository.reveal_secret(uuid4(), "https://never-saved.test") is None


@pytest.mark.asyncio
async def test_the_stored_secret_is_what_comes_back() -> None:
    """The one method that returns a secret has to actually round-trip it."""
    cipher = get_secret_cipher()
    encrypted = await cipher.encrypt_json_async(
        {
            "state": STATE,
            "username": "a@b.test",
            "password": PASSWORD,
            "totp_seed": "JBSWY3DP",
        }
    )

    class _Row:
        secret = encrypted

    repository = WebLoginRepository(_FakeSession(existing=_Row()), cipher)
    revealed = await repository.reveal_secret(uuid4(), "https://app.example.com")

    assert revealed is not None
    assert revealed.password == PASSWORD
    assert revealed.totp_seed == "JBSWY3DP"
    assert revealed.state == STATE


@pytest.mark.asyncio
async def test_marking_used_is_silent_when_there_is_nothing_to_mark() -> None:
    """A login deleted between choosing it and recording the use is ordinary."""
    repository = WebLoginRepository(_FakeSession(), get_secret_cipher())
    await repository.mark_used(uuid4(), "https://gone.test")


@pytest.mark.asyncio
async def test_marking_used_stamps_the_row() -> None:
    class _Row:
        last_used_at = None

    row = _Row()
    repository = WebLoginRepository(_FakeSession(existing=row), get_secret_cipher())
    await repository.mark_used(uuid4(), "https://app.example.com")
    assert row.last_used_at is not None


@pytest.mark.asyncio
async def test_a_missing_origin_reads_as_none() -> None:
    repository = WebLoginRepository(_FakeSession(), get_secret_cipher())
    assert await repository.get_for_origin(uuid4(), "https://nope.test") is None


@pytest.mark.asyncio
async def test_an_empty_detail_is_stored_as_nothing() -> None:
    """`""` and "nothing to say" are the same fact; keeping both invites a
    reader to wonder which one they are looking at."""
    session = _FakeSession()
    repository = WebLoginRepository(session, get_secret_cipher())
    await repository.record(
        user_id=uuid4(), origin="https://x.test", action="save", outcome="ok", detail=""
    )
    assert session.added[0].detail is None
