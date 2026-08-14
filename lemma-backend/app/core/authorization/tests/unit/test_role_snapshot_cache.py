"""Serialization round-trip for the Redis-backed role-snapshot cache.

The cache stores RoleSnapshot as JSON in Redis; a serialization bug would corrupt
authorization decisions, so verify the pure (de)serialization round-trips exactly,
including None scopes and the nested PrincipalRef sets.
"""

from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.authorization import cache as cache_module
from app.core.authorization.cache import RoleSnapshot, _deserialize, _serialize
from app.core.authorization.context import PrincipalRef


def test_role_snapshot_serialization_round_trips():
    p1 = PrincipalRef(type="user", id=uuid4())
    p2 = PrincipalRef(type="role", id=uuid4())
    snapshot = RoleSnapshot(
        organization_id=uuid4(),
        pod_id=uuid4(),
        role_ids=frozenset({uuid4(), uuid4()}),
        role_names=frozenset({"admin", "viewer"}),
        permission_ids=frozenset({"file.read", "file.write"}),
        principal_refs=frozenset({p1, p2}),
        grant_principal_sets=(frozenset({p1}), frozenset({p1, p2})),
    )
    assert _deserialize(_serialize(snapshot)) == snapshot


def test_role_snapshot_serialization_handles_empty_and_none_scopes():
    snapshot = RoleSnapshot(
        organization_id=None,
        pod_id=None,
        role_ids=frozenset(),
        role_names=frozenset(),
        permission_ids=frozenset(),
        principal_refs=frozenset(),
        grant_principal_sets=(),
    )
    assert _deserialize(_serialize(snapshot)) == snapshot


class _BrokenCache:
    async def get_raw(self, suffix):
        raise ConnectionError("redis down")

    async def set_raw(self, suffix, payload, ttl_seconds=None):
        raise ConnectionError("redis down")


@pytest.mark.asyncio
async def test_redis_outage_degrades_to_miss_and_reports_bounded_incident(monkeypatch):
    """Redis failure remains non-fatal and feeds the incident aggregator."""
    incident = Mock()
    monkeypatch.setattr(cache_module, "_get_role_cache", lambda: _BrokenCache())
    monkeypatch.setattr(cache_module, "_role_cache_incident", incident)
    result = await cache_module.get_role_snapshot(
        user_id=uuid4(), organization_id=None, pod_id=None
    )
    await cache_module.set_role_snapshot(
        user_id=uuid4(),
        snapshot=RoleSnapshot(
            organization_id=None,
            pod_id=None,
            role_ids=frozenset(),
            role_names=frozenset(),
            permission_ids=frozenset(),
            principal_refs=frozenset(),
            grant_principal_sets=(),
        ),
    )
    assert result is None
    assert incident.record_failure.call_count == 2
    incident.record_failure.assert_called_with(error_type="ConnectionError")


class _RecordingCache:
    def __init__(self):
        self.deleted_prefixes: list[str] = []
        self.deleted_indexes: list[str] = []
        self.indexed: dict[str, set[str]] = {}
        self.cleared = 0

    async def track_in_index(self, index_suffix: str, suffix: str) -> None:
        self.indexed.setdefault(index_suffix, set()).add(suffix)

    async def delete_indexed(self, index_suffix: str) -> int:
        self.deleted_indexes.append(index_suffix)
        return len(self.indexed.pop(index_suffix, ()))

    async def delete_prefix(self, sub_prefix: str) -> None:
        self.deleted_prefixes.append(sub_prefix)

    async def clear_prefix(self) -> None:
        self.cleared += 1


@pytest.mark.asyncio
async def test_invalidate_with_user_id_targets_only_that_principal(monkeypatch):
    recorder = _RecordingCache()
    monkeypatch.setattr(cache_module, "_get_role_cache", lambda: recorder)
    user_id = uuid4()

    # A snapshot this principal owns, recorded in its index.
    await recorder.track_in_index(str(user_id), f"{user_id}:-:{uuid4()}")

    await cache_module.invalidate_role_snapshot_cache(
        organization_id=uuid4(), pod_id=uuid4(), user_id=user_id
    )

    # Deleted by the principal's own list, not by scanning the keyspace.
    assert recorder.deleted_indexes == [str(user_id)]
    assert recorder.deleted_prefixes == []
    assert recorder.cleared == 0


@pytest.mark.asyncio
async def test_invalidate_falls_back_to_the_scan_when_no_index_exists(monkeypatch):
    """The index expires with the entries, and old snapshots predate it.

    Falling back keeps invalidation correct for a principal whose snapshot was
    written before the index existed — correctness first, speed when it can.
    """
    recorder = _RecordingCache()
    monkeypatch.setattr(cache_module, "_get_role_cache", lambda: recorder)
    user_id = uuid4()

    await cache_module.invalidate_role_snapshot_cache(user_id=user_id)

    assert recorder.deleted_indexes == [str(user_id)]
    assert recorder.deleted_prefixes == [f"{user_id}:"]


@pytest.mark.asyncio
async def test_invalidate_without_user_id_clears_everything(monkeypatch):
    recorder = _RecordingCache()
    monkeypatch.setattr(cache_module, "_get_role_cache", lambda: recorder)

    await cache_module.invalidate_role_snapshot_cache(
        organization_id=uuid4(), pod_id=uuid4()
    )

    assert recorder.cleared == 1
    assert recorder.deleted_prefixes == []
