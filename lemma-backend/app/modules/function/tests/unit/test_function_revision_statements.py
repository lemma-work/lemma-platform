"""Deduplicate retained revisions without reviving a removed storage generation."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.function.domain.entities import FunctionRevisionEntity
from app.modules.function.infrastructure.repositories import FunctionRepository

from app.modules.test_support.mappers import configure_test_mappers

configure_test_mappers()

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, model):
        self._model = model

    def scalar_one(self):
        return self._model

    def scalar_one_or_none(self):
        return self._model


class _Session:
    """Records every statement, and refuses to hydrate a read-back."""

    def __init__(self, model):
        self._model = model
        self.statements: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement).lower())
        return _Result(self._model)


class _Model:
    def __init__(self, entity):
        self._entity = entity

    def to_entity(self):
        return self._entity


def _repository(entity):
    repository = FunctionRepository.__new__(FunctionRepository)
    repository.session = _Session(_Model(entity))
    return repository


def _entity(function_id, *, pruned_at=None):
    return FunctionRevisionEntity(
        id=uuid4(),
        function_id=function_id,
        revision_number=0,
        revision_hash="sha256:" + "a" * 64,
        code_path=f"revisions/{'a' * 64}/function.py",
        input_schema={},
        output_schema={},
        pruned_at=pruned_at,
    )


@pytest.mark.asyncio
async def test_deduplication_only_targets_retained_revisions():
    function_id = uuid4()
    repository = _repository(_entity(function_id))

    await repository.record_revision(_entity(function_id))

    upsert = repository.session.statements[-1]
    assert "on conflict (function_id, revision_hash) where pruned_at is null" in upsert
    assert "do update set" in upsert
    set_clause = upsert.split("do update set", 1)[1].split("returning", 1)[0]
    assert "pruned_at" not in set_clause
    # Updating these would reorder the history rather than restore a build; the
    # stored code_path and schemas are already right, because the hash covers
    # the artifact they were extracted from.
    for column in ("revision_number", "created_at", "created_by", "code_path"):
        assert column not in set_clause


@pytest.mark.asyncio
async def test_recording_a_revision_never_reads_the_row_back():
    """DO UPDATE ... RETURNING yields a row on both paths, so the fallback
    SELECT -- and the assert that could fire instead of it -- are gone."""
    function_id = uuid4()
    repository = _repository(_entity(function_id))

    await repository.record_revision(_entity(function_id))

    statements = repository.session.statements
    assert len(statements) == 2, statements  # the row lock, then the upsert
    assert "for update" in statements[0]
    assert not any(
        s.startswith("select") and "function_revisions" in s for s in statements
    )


@pytest.mark.asyncio
async def test_the_revision_number_is_allocated_under_the_function_row_lock():
    """Two concurrent saves of DIFFERENT code both compute max+1 under READ
    COMMITTED and collide on uq_function_revision_number. The lock is what stops
    that, and taking it here stops the numbering depending on an ordering two
    layers up."""
    function_id = uuid4()
    repository = _repository(_entity(function_id))

    await repository.record_revision(_entity(function_id))

    lock, upsert = repository.session.statements
    assert "functions" in lock and "for update" in lock
    assert "max(function_revisions.revision_number)" in upsert
