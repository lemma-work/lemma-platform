from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.modules.web_login.domain.entities import WebLoginKind
from app.modules.web_login.services.capture import capture_session

STATE = json.dumps({"cookies": [{"name": "sid", "value": "s3cr3t"}]})


class _FakeSession:
    def __init__(self, *, export_ok: bool = True, payload: bytes | None = None):
        self.commands: list[str] = []
        self._export_ok = export_ok
        self._payload = STATE.encode() if payload is None else payload

    async def exec_command(self, *, cmd, timeout=None, **kwargs):
        self.commands.append(cmd)
        if "state save" in cmd and not self._export_ok:
            return {"success": False, "exit_code": 1}
        return {"success": True, "exit_code": 0}

    async def read_file(self, path, **kwargs):
        return self._payload


class _FakeRepository:
    def __init__(self):
        self.saved: list[dict] = []
        self.records: list[dict] = []

    async def save(self, **kwargs):
        self.saved.append(kwargs)

        class _Row:
            id = uuid4()

        return _Row()

    async def record(self, **kwargs):
        self.records.append(kwargs)


@pytest.mark.asyncio
async def test_a_signed_in_browser_is_remembered() -> None:
    session, repository = _FakeSession(), _FakeRepository()

    outcome = await capture_session(
        session,
        repository,
        user_id=uuid4(),
        origin="https://app.example.com",
        label="Example",
    )

    assert outcome.saved is True
    assert repository.saved[0]["kind"] is WebLoginKind.SESSION
    assert repository.saved[0]["secret"].state == STATE
    assert repository.records[0]["action"] == "capture"


@pytest.mark.asyncio
async def test_the_export_never_lingers_in_the_sandbox() -> None:
    session, repository = _FakeSession(), _FakeRepository()

    await capture_session(
        session, repository, user_id=uuid4(), origin="https://x.test", label="x"
    )

    assert any(cmd.startswith("rm -f ") for cmd in session.commands)


@pytest.mark.asyncio
async def test_an_error_message_is_not_stored_as_a_login() -> None:
    """Otherwise the person gets an item that looks fine in the list and fails
    every single time it is used."""
    session = _FakeSession(payload=b"Error: browser not running")
    repository = _FakeRepository()

    outcome = await capture_session(
        session, repository, user_id=uuid4(), origin="https://x.test", label="x"
    )

    assert outcome.saved is False
    assert repository.saved == []
    assert "nothing that looks like a session" in outcome.reason


@pytest.mark.asyncio
async def test_a_failed_export_says_so_rather_than_saving_nothing_quietly() -> None:
    session = _FakeSession(export_ok=False)
    repository = _FakeRepository()

    outcome = await capture_session(
        session, repository, user_id=uuid4(), origin="https://x.test", label="x"
    )

    assert outcome.saved is False
    assert repository.saved == []


@pytest.mark.asyncio
async def test_an_implausibly_large_export_is_refused() -> None:
    session = _FakeSession(payload=b"x" * (3 * 1024 * 1024))
    repository = _FakeRepository()

    outcome = await capture_session(
        session, repository, user_id=uuid4(), origin="https://x.test", label="x"
    )

    assert outcome.saved is False
    assert "implausibly large" in outcome.reason
