"""Unit tests for applier helpers — idempotency conflict detection."""

from __future__ import annotations

from app.modules.pod_import.infrastructure.applier import _is_already_exists


class AgentAlreadyExistsError(Exception):
    pass


class DatastoreConflictError(Exception):
    pass


def test_conflict_exceptions_are_treated_as_already_exists():
    assert _is_already_exists(AgentAlreadyExistsError("mr-toot"))
    assert _is_already_exists(DatastoreConflictError("nope"))
    assert _is_already_exists(ValueError("Table 'commitments' already exists in this datastore"))


def test_real_errors_are_not_swallowed():
    assert not _is_already_exists(ValueError("invalid column type"))
    assert not _is_already_exists(RuntimeError("connector timeout"))
