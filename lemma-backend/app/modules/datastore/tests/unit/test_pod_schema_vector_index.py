"""A pod whose vector index failed to build has to say so, at warning.

Production runs at LOG_LEVEL=INFO, so a caught failure logged at debug is the
same as one that was never logged. A pod whose vector index never built keeps
answering searches by sequential scan and simply gets slower as it grows --
until `guard_query_plan`'s cost ceiling starts refusing unrelated queries in
the same schema. The build is the one statement of the version-1 step that may
fail without failing the step, so this is the only place the fault surfaces.
"""

from __future__ import annotations

import logging

import pytest

from app.modules.datastore.infrastructure import pod_schema_migrations

_EVENT = "datastore.pod_schema_migrations.vector_index_build.degraded"


class _Savepoint:
    def __init__(self) -> None:
        self.rolled_back = False
        self.committed = False

    async def rollback(self) -> None:
        self.rolled_back = True

    async def commit(self) -> None:
        self.committed = True


class _Connection:
    """Accepts every statement of the step except the vector index build."""

    def __init__(self, failure: str | None) -> None:
        self._failure = failure
        self.statements: list[str] = []
        self.savepoint = _Savepoint()

    async def execute(self, statement, *_args, **_kwargs) -> None:
        sql = str(statement)
        self.statements.append(sql)
        if self._failure is not None and "USING hnsw" in sql:
            raise RuntimeError(self._failure)

    async def begin_nested(self) -> _Savepoint:
        return self.savepoint


async def _run_v1(caplog, failure: str | None) -> tuple[list[dict], _Connection]:
    connection = _Connection(failure)
    with caplog.at_level(logging.DEBUG):
        await pod_schema_migrations._v1_chunks(connection, "pod_test_schema")  # type: ignore[arg-type]
    warnings = [
        record.msg
        for record in caplog.records
        if record.levelno >= logging.WARNING and isinstance(record.msg, dict)
    ]
    return warnings, connection


@pytest.mark.asyncio
async def test_a_refused_build_is_reported_at_warning_with_the_schema(caplog) -> None:
    warnings, connection = await _run_v1(caplog, "permission denied for schema")

    build = next(w for w in warnings if w["event"] == _EVENT)
    assert build["schema_name"] == "pod_test_schema"
    assert "permission denied for schema" in build["error_traceback"]
    # The index is lost, not the table: the savepoint is what is rolled back.
    assert connection.savepoint.rolled_back
    assert not connection.savepoint.committed


@pytest.mark.asyncio
async def test_a_missing_extension_stays_quiet(caplog) -> None:
    """An install without pgvector's halfvec is a deployment choice, not a
    fault, and it would otherwise warn once per pod on every process."""
    warnings, connection = await _run_v1(caplog, 'extension "vector" does not exist')

    assert not [w for w in warnings if w["event"] == _EVENT]
    assert connection.savepoint.rolled_back


@pytest.mark.asyncio
async def test_a_successful_build_commits_its_savepoint_and_says_nothing(
    caplog,
) -> None:
    warnings, connection = await _run_v1(caplog, None)

    assert not warnings
    assert connection.savepoint.committed and not connection.savepoint.rolled_back
    assert any("USING hnsw" in sql for sql in connection.statements)
