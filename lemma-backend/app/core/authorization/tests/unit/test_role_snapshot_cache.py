"""Serialization round-trip for the Redis-backed role-snapshot cache.

The cache stores RoleSnapshot as JSON in Redis; a serialization bug would corrupt
authorization decisions, so verify the pure (de)serialization round-trips exactly,
including None scopes and the nested PrincipalRef sets.
"""

from uuid import uuid4

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
