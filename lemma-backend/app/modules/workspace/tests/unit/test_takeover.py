from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.workspace.api.controllers import takeover_controller as controller
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.takeover import (
    TakeoverNotFound,
    TakeoverStatus,
    TakeoverStore,
)


class _FakeRedis:
    """Enough Redis for the store: string get/set with a TTL flag."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.kept_ttl: list[str] = []

    async def set(self, key, value, ex=None, keepttl=False):
        if keepttl:
            self.kept_ttl.append(key)
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)


def _store() -> tuple[TakeoverStore, _FakeRedis]:
    redis = _FakeRedis()
    store = TakeoverStore()
    store._redis = redis  # the connection is the collaborator, not the logic
    return store, redis


@pytest.mark.asyncio
async def test_a_request_round_trips_for_its_owner() -> None:
    store, _ = _store()
    owner = uuid4()
    conversation = uuid4()

    created = await store.create(
        user_id=owner,
        conversation_id=conversation,
        origin="https://app.example.com",
        reason="the reports page needs a login",
    )
    found = await store.get_for_user(created.request_id, owner)

    assert found.origin == "https://app.example.com"
    assert found.conversation_id == conversation
    assert found.status is TakeoverStatus.PENDING
    assert found.is_open is True


@pytest.mark.asyncio
async def test_somebody_elses_request_reads_as_missing() -> None:
    """Not "forbidden": the id is unguessable, so telling the difference only
    confirms an id to somebody who should not have one."""
    store, _ = _store()
    created = await store.create(
        user_id=uuid4(), conversation_id=None, origin="https://x.test", reason=""
    )

    with pytest.raises(TakeoverNotFound):
        await store.get_for_user(created.request_id, uuid4())


@pytest.mark.asyncio
async def test_an_unknown_id_reads_as_missing() -> None:
    store, _ = _store()
    with pytest.raises(TakeoverNotFound):
        await store.get_for_user("nope", uuid4())


@pytest.mark.asyncio
async def test_resolving_keeps_the_remaining_ttl() -> None:
    """Resolving ends the exchange; it is not a reason to hold the record open
    longer than it already had."""
    store, redis = _store()
    owner = uuid4()
    created = await store.create(
        user_id=owner, conversation_id=None, origin="https://x.test", reason=""
    )

    resolved = await store.resolve(
        created.request_id, owner, status=TakeoverStatus.DONE
    )

    assert resolved.status is TakeoverStatus.DONE
    assert resolved.is_open is False
    assert redis.kept_ttl == [f"workspace:takeover:v1:{created.request_id}"]
    stored = json.loads(redis.values[f"workspace:takeover:v1:{created.request_id}"])
    assert stored["status"] == "done"


@pytest.mark.asyncio
async def test_resolving_somebody_elses_request_is_refused() -> None:
    store, _ = _store()
    created = await store.create(
        user_id=uuid4(), conversation_id=None, origin="https://x.test", reason=""
    )
    with pytest.raises(TakeoverNotFound):
        await store.resolve(created.request_id, uuid4(), status=TakeoverStatus.DONE)


# --- controller -------------------------------------------------------------


class _MissingStore:
    async def get_for_user(self, request_id, user_id):
        raise TakeoverNotFound(request_id)

    async def resolve(self, request_id, user_id, *, status):
        raise TakeoverNotFound(request_id)


class _FakeService:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    async def create_browser_access(self, user_id, *, ttl_seconds):
        return SimpleNamespace(
            url="https://api.test/workspace-ports/tok/",
            expires_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )

    async def get_session(self, user_id, **kwargs):
        service = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def exec_command(self, *, cmd, timeout=None):
                service.commands.append(cmd)
                return {"success": True}

        return _Session()

    async def close(self):
        self.closed = True


def _user():
    return SimpleNamespace(id=uuid4())


@pytest.mark.asyncio
async def test_opening_an_expired_takeover_is_a_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)

    with pytest.raises(HTTPException) as raised:
        await controller.open_takeover("gone", _user(), _MissingStore(), _FakeService())
    assert raised.value.status_code == 404


@pytest.mark.asyncio
async def test_opening_a_takeover_mints_a_live_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", "k" * 32)
    store, _ = _store()
    owner = uuid4()
    created = await store.create(
        user_id=owner,
        conversation_id=None,
        origin="https://app.example.com",
        reason="reports need a login",
    )
    service = _FakeService()

    opened = await controller.open_takeover(
        created.request_id, SimpleNamespace(id=owner), store, service
    )

    assert opened.origin == "https://app.example.com"
    assert opened.url.startswith("https://api.test/workspace-ports/")
    assert service.closed is True


@pytest.mark.asyncio
async def test_without_a_signing_key_there_is_nothing_to_hand_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", None)

    with pytest.raises(HTTPException) as raised:
        await controller.open_takeover("any", _user(), _MissingStore(), _FakeService())
    assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_a_heartbeat_touches_the_browser() -> None:
    """Chrome closes after two minutes idle — shorter than a second factor."""
    store, _ = _store()
    owner = uuid4()
    created = await store.create(
        user_id=owner, conversation_id=None, origin="https://x.test", reason=""
    )
    service = _FakeService()

    await controller.heartbeat_takeover(
        created.request_id, SimpleNamespace(id=owner), store, service
    )

    assert service.commands == ["agent-browser get url"]
    assert service.closed is True


@pytest.mark.asyncio
async def test_a_heartbeat_for_somebody_elses_takeover_is_refused() -> None:
    store, _ = _store()
    created = await store.create(
        user_id=uuid4(), conversation_id=None, origin="https://x.test", reason=""
    )
    service = _FakeService()

    with pytest.raises(HTTPException) as raised:
        await controller.heartbeat_takeover(created.request_id, _user(), store, service)

    assert raised.value.status_code == 404
    assert service.commands == []
