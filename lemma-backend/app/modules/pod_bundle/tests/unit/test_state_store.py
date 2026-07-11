"""State store semantics against an in-memory fake of RedisJsonCache."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.exceptions import RedisError

from app.modules.pod_bundle.domain.state import (
    BundleJobKind,
    BundleSource,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.domain.errors import BundleStateConflictError
from app.modules.pod_bundle.infrastructure.state_store import PodBundleStateStore
from app.modules.pod_bundle.infrastructure import state_store as state_store_module


class FakeJsonCache:
    """Duck-typed stand-in for RedisJsonCache (get_json/set_json/delete)."""

    def __init__(self):
        self.data: dict[str, object] = {}
        self.ttl_writes: list[str] = []

    async def get_json(self, suffix: str):
        return self.data.get(suffix)

    async def set_json(self, suffix: str, value, *, ttl_seconds=None):
        # RedisJsonCache always (re)sets EX on write — every save refreshes
        # the TTL, which is the "6h past last activity" behavior.
        self.data[suffix] = value
        self.ttl_writes.append(suffix)

    async def delete(self, suffix: str):
        self.data.pop(suffix, None)

    async def close(self):
        pass


def _state() -> ImportState:
    return ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(kind="URL"),
    )


@pytest.fixture
def cache() -> FakeJsonCache:
    return FakeJsonCache()


@pytest.fixture
def store(cache: FakeJsonCache) -> PodBundleStateStore:
    return PodBundleStateStore(cache=cache)


async def test_save_and_get_round_trip(store: PodBundleStateStore):
    state = _state()
    await store.save_import(state)

    loaded = await store.get_import(state.import_id)
    assert loaded is not None
    assert loaded.import_id == state.import_id
    assert loaded.status == ImportStatus.QUEUED


async def test_missing_key_returns_none(store: PodBundleStateStore):
    assert await store.get_import(uuid4()) is None


async def test_every_save_bumps_seq_and_refreshes_ttl(
    store: PodBundleStateStore, cache: FakeJsonCache
):
    state = _state()
    await store.save_import(state)
    assert state.seq == 1

    state.status = ImportStatus.PLANNING
    await store.save_import(state)
    assert state.seq == 2

    loaded = await store.get_import(state.import_id)
    assert loaded.seq == 2
    # Two writes = two TTL refreshes (RedisJsonCache sets EX on every set).
    assert len(cache.ttl_writes) == 2


async def test_delete_removes_document(store: PodBundleStateStore):
    state = _state()
    await store.save_import(state)
    await store.delete_import(state.import_id)
    assert await store.get_import(state.import_id) is None


async def test_kinds_are_namespaced(store: PodBundleStateStore, cache: FakeJsonCache):
    state = _state()
    await store.save_import(state)
    # An export lookup with the same UUID must not see the import document.
    assert await store.get_export(state.import_id) is None
    assert f"import:{state.import_id}" in cache.data


class _Session:
    def __init__(self, model=None):
        self.model = model

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, model_type, import_id):
        del model_type, import_id
        return self.model


async def test_durable_get_prefers_postgres_snapshot(monkeypatch, store):
    state = _state()
    heartbeat = datetime.now(timezone.utc)
    session = _Session(
        SimpleNamespace(
            job_kind=BundleJobKind.IMPORT.value,
            snapshot=state.model_dump(mode="json"),
            version=4,
            attempt=2,
            heartbeat_at=heartbeat,
        )
    )
    monkeypatch.setattr(state_store_module, "async_session_maker", lambda: session)
    store._durable = True

    loaded = await store.get_import(state.import_id)

    assert loaded is not None
    assert loaded.import_id == state.import_id
    assert loaded.status == state.status
    assert loaded.version == 4
    assert loaded.attempt == 2
    assert loaded.heartbeat_at == heartbeat


async def test_durable_get_contains_invalid_legacy_cache(monkeypatch, store):
    class _FailingCache(FakeJsonCache):
        async def get_json(self, suffix: str):
            del suffix
            raise RedisError("redis unavailable")

    durable = PodBundleStateStore(cache=_FailingCache())
    durable._durable = True
    monkeypatch.setattr(
        state_store_module, "async_session_maker", lambda: _Session(None)
    )

    assert await durable.get_import(uuid4()) is None


async def test_durable_get_imports_legacy_redis_snapshot(monkeypatch, store, cache):
    state = _state()
    cache.data[f"import:{state.import_id}"] = state.model_dump(mode="json")
    store._durable = True
    monkeypatch.setattr(
        state_store_module, "async_session_maker", lambda: _Session(None)
    )
    persisted = state.model_copy(deep=True)
    persisted.version = 1
    persist = AsyncMock(return_value=persisted)
    monkeypatch.setattr(store, "_persist_state", persist)

    loaded = await store.get_import(state.import_id)

    assert loaded is not None
    assert loaded.import_id == state.import_id
    assert loaded.version == 1
    persist.assert_awaited_once_with(
        BundleJobKind.IMPORT,
        state.import_id,
        loaded,
    )


async def test_durable_save_does_not_mirror_state_conflict(
    monkeypatch, store, cache
):
    state = _state()
    store._durable = True
    monkeypatch.setattr(
        store,
        "_persist_state",
        AsyncMock(side_effect=BundleStateConflictError()),
    )

    with pytest.raises(BundleStateConflictError):
        await store.save_import(state)

    assert cache.data == {}


async def test_durable_save_survives_redis_mirror_failure(monkeypatch):
    class _FailingCache(FakeJsonCache):
        async def set_json(self, suffix: str, value, *, ttl_seconds=None):
            del suffix, value, ttl_seconds
            raise RedisError("redis unavailable")

    store = PodBundleStateStore(cache=_FailingCache())
    store._durable = True
    state = _state()
    persisted = state.model_copy(deep=True)
    persisted.version = 1
    monkeypatch.setattr(store, "_persist_state", AsyncMock(return_value=persisted))

    await store.save_import(state)
    assert state.version == 1


def test_transition_refuses_to_overwrite_terminal_state():
    existing = _state()
    existing.status = ImportStatus.CANCELLED
    incoming = existing.model_copy(deep=True)
    incoming.status = ImportStatus.APPLYING

    with pytest.raises(BundleStateConflictError):
        PodBundleStateStore._validate_transition(
            BundleJobKind.IMPORT,
            existing,
            incoming,
            allow_failed_reopen=False,
        )


def test_transition_rejects_stale_worker_after_cancellation():
    existing = _state()
    existing.status = ImportStatus.CANCELLING
    existing.cancel_requested_at = datetime.now(timezone.utc)
    existing.committed_steps = [2]
    incoming = existing.model_copy(deep=True)
    incoming.status = ImportStatus.APPLYING
    incoming.cancel_requested_at = None
    incoming.committed_steps = [1]

    with pytest.raises(BundleStateConflictError):
        PodBundleStateStore._validate_transition(
            BundleJobKind.IMPORT,
            existing,
            incoming,
            allow_failed_reopen=False,
        )


def test_failed_job_reopens_only_with_explicit_incremented_attempt():
    existing = _state()
    existing.status = ImportStatus.FAILED
    incoming = existing.model_copy(deep=True)
    incoming.status = ImportStatus.APPLYING

    with pytest.raises(BundleStateConflictError):
        PodBundleStateStore._validate_transition(
            BundleJobKind.IMPORT,
            existing,
            incoming,
            allow_failed_reopen=True,
        )

    incoming.attempt += 1
    PodBundleStateStore._validate_transition(
        BundleJobKind.IMPORT,
        existing,
        incoming,
        allow_failed_reopen=True,
    )
