"""State store semantics against an in-memory fake of RedisJsonCache."""

from uuid import uuid4

import pytest

from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    ImportStatus,
)
from app.modules.pod_bundle.infrastructure.state_store import PodBundleStateStore


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
        source=BundleSource(kind="upload"),
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
