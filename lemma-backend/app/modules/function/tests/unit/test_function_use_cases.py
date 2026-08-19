"""Invariant tests for the function use-case layer.

These lock the properties the function redesign bought, in the fast (mock) gate:
- the pooled DB connection is NOT held across the sandbox round-trip (create
  schema extraction + API execute),
- the worker path executes a run with NO ctx (trusting the persisted run),
- a JOB dispatch returns PENDING + does not run the sandbox inline.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.application.function_use_cases import FunctionUseCases
from app.modules.function.domain.entities import (
    FunctionArtifact,
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionSchemaSet,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.domain.errors import (
    FunctionRunQueueUnavailable,
    FunctionValidationError,
)
from app.modules.function.services.function_service import ResolvedExecution
from app.modules.function.services.function_service import (
    LegacyFunctionRevisionRequired,
)

pytestmark = pytest.mark.asyncio


class _TrackingUowFactory:
    """A ``uow_factory`` whose context manager flips a shared ``open`` flag, so a
    test can observe whether a pooled connection is held during a given call."""

    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_, *exc):
                state["open"] = False
                return False

        return _Cm()


@pytest.fixture(autouse=True)
def _stub_pod_context(monkeypatch):
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=SimpleNamespace(require=AsyncMock())),
    )


def _function(**overrides) -> FunctionEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "fn",
        "type": FunctionType.API,
        "status": FunctionStatus.DRAFT,
    }
    payload.update(overrides)
    return FunctionEntity(**payload)


@pytest.mark.asyncio
async def test_create_extracts_schemas_with_no_connection_held(monkeypatch):
    factory = _TrackingUowFactory()
    created = _function(name="with-code")
    service = SimpleNamespace(
        resolve_create=AsyncMock(return_value=created),
        persist_create=AsyncMock(return_value=created),
        get_function_by_name=AsyncMock(return_value=created),
    )
    compiler = SimpleNamespace(
        write_code=AsyncMock(),
        extract_schemas=AsyncMock(),
        build_artifact=AsyncMock(),
    )
    dispatcher = SimpleNamespace(execute=AsyncMock(), cancel=AsyncMock())
    captured = {}
    operations: list[str] = []

    async def _fake_extract(function, artifact, *, user_id):
        captured["open"] = factory.state["open"]
        assert function is created
        assert artifact.revision_hash == f"sha256:{'b' * 64}"
        assert user_id == created.user_id
        operations.append("schemas")
        return FunctionSchemaSet(input={"a": 1}, output={"b": 2})

    compiler.extract_schemas.side_effect = _fake_extract

    async def _fake_build(function, code, *, python_packages):
        assert factory.state["open"] is False
        assert python_packages == ()
        operations.append("artifact")
        return FunctionArtifact(revision_hash=f"sha256:{'b' * 64}")

    compiler.build_artifact.side_effect = _fake_build

    async def _fake_write(function_id, path, code):
        assert factory.state["open"] is False
        assert function_id == created.id
        assert path == f"revisions/{'b' * 64}/function.py"
        operations.append("source")

    compiler.write_code.side_effect = _fake_write

    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        compiler,
        dispatcher,
        AsyncMock(),
    )
    result = await use_cases.create_function(
        pod_id=created.pod_id,
        entity=created,
        user_id=created.user_id,
        code="def run(): ...",
        request=SimpleNamespace(),
    )

    # Schema extraction (a sandbox round-trip) ran with no pooled connection held.
    assert captured["open"] is False
    assert factory.state["open"] is False
    # Resolve (insert) + persist happened in distinct short UoWs.
    assert factory.state["opens"] >= 2
    assert operations == ["artifact", "schemas", "source"]
    assert created.code_path == f"revisions/{'b' * 64}/function.py"
    assert result is created


@pytest.mark.asyncio
async def test_execute_api_touches_sandbox_with_no_connection_held(monkeypatch):
    factory = _TrackingUowFactory()
    function = _function(name="api-fn", type=FunctionType.API)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        input_data={"x": 1},
        status=FunctionRunStatus.PENDING,
    )
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            return_value=ResolvedExecution(function=function, run=run)
        )
    )

    captured = {}
    failed = run.model_copy(update={"status": FunctionRunStatus.FAILED})

    async def _dispatch(run_id, **kwargs):
        captured["open"] = factory.state["open"]
        captured["mode"] = kwargs["mode"]
        assert run_id == run.id
        return failed

    dispatcher = SimpleNamespace(
        execute=AsyncMock(side_effect=_dispatch),
        cancel=AsyncMock(),
    )
    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        SimpleNamespace(),
        dispatcher,
        AsyncMock(),
    )
    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name="api-fn",
        input_data={"x": 1},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    assert captured["open"] is False
    assert captured["mode"] == FunctionDispatchMode.SYNCHRONOUS
    assert result.status == FunctionRunStatus.FAILED
    assert factory.state["open"] is False


@pytest.mark.asyncio
async def test_execute_backfills_legacy_revision_before_creating_run():
    factory = _TrackingUowFactory()
    function = _function(
        name="legacy-fn",
        type=FunctionType.API,
        status=FunctionStatus.READY,
        code_path="legacy-fn.py",
        revision_hash=None,
    )
    revision_hash = f"sha256:{'c' * 64}"
    activated = function.model_copy(
        update={
            "revision_hash": revision_hash,
            "code_path": f"revisions/{'c' * 64}/function.py",
        }
    )
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        status=FunctionRunStatus.PENDING,
    )
    resolved = ResolvedExecution(function=activated, run=run)
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            side_effect=[
                LegacyFunctionRevisionRequired(function),
                resolved,
            ]
        ),
        activate_revision_if_missing=AsyncMock(return_value=activated),
    )
    code = (
        "# input_type_name: Input\n"
        "# output_type_name: Output\n"
        "# function_name: run\n"
    )
    compiler = SimpleNamespace(
        read_code=AsyncMock(return_value=code),
        build_artifact=AsyncMock(
            return_value=FunctionArtifact(revision_hash=revision_hash)
        ),
        write_code=AsyncMock(),
    )
    completed = run.model_copy(update={"status": FunctionRunStatus.COMPLETED})
    dispatcher = SimpleNamespace(
        execute=AsyncMock(return_value=completed),
        cancel=AsyncMock(),
    )
    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        compiler,
        dispatcher,
        AsyncMock(),
    )

    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name=function.name,
        input_data={},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    assert result.status == FunctionRunStatus.COMPLETED
    compiler.read_code.assert_awaited_once_with(function.id, "legacy-fn.py")
    compiler.build_artifact.assert_awaited_once_with(
        function,
        code,
        python_packages=(),
    )
    compiler.write_code.assert_awaited_once_with(
        function.id,
        f"revisions/{'c' * 64}/function.py",
        code,
    )
    service.activate_revision_if_missing.assert_awaited_once_with(
        function.id,
        expected_code_path="legacy-fn.py",
        revision_hash=revision_hash,
        code_path=f"revisions/{'c' * 64}/function.py",
    )
    assert service.resolve_execute.await_count == 2
    assert factory.state["opens"] == 3


@pytest.mark.asyncio
async def test_execute_raises_when_a_second_backfill_attempt_is_still_legacy():
    """The success case above backfills once and the retried ``resolve_once``
    succeeds. If it *still* reports the function as pre-artifact -- a
    concurrent racer reverted the activation, or the backfill silently landed
    on the wrong row -- retrying forever would spin; this must give up with a
    clear, distinct error instead of leaking the internal control-flow
    exception."""
    factory = _TrackingUowFactory()
    function = _function(
        name="legacy-fn",
        type=FunctionType.API,
        status=FunctionStatus.READY,
        code_path="legacy-fn.py",
        revision_hash=None,
    )
    revision_hash = f"sha256:{'d' * 64}"
    activated = function.model_copy(
        update={
            "revision_hash": revision_hash,
            "code_path": f"revisions/{'d' * 64}/function.py",
        }
    )
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            side_effect=[
                LegacyFunctionRevisionRequired(function),
                LegacyFunctionRevisionRequired(function),
            ]
        ),
        activate_revision_if_missing=AsyncMock(return_value=activated),
    )
    code = (
        "# input_type_name: Input\n"
        "# output_type_name: Output\n"
        "# function_name: run\n"
    )
    compiler = SimpleNamespace(
        read_code=AsyncMock(return_value=code),
        build_artifact=AsyncMock(
            return_value=FunctionArtifact(revision_hash=revision_hash)
        ),
        write_code=AsyncMock(),
    )
    dispatcher = SimpleNamespace(execute=AsyncMock(), cancel=AsyncMock())
    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        compiler,
        dispatcher,
        AsyncMock(),
    )

    with pytest.raises(
        FunctionValidationError, match="did not activate an executable revision"
    ):
        await use_cases.execute_function(
            pod_id=function.pod_id,
            name=function.name,
            input_data={},
            user_id=function.user_id,
            user_email=None,
            request=SimpleNamespace(),
        )

    # The backfill itself ran exactly once -- a second raise after that is
    # reported, never silently retried.
    assert service.resolve_execute.await_count == 2
    compiler.build_artifact.assert_awaited_once()
    dispatcher.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_run_by_id_worker_path_needs_no_ctx():
    factory = _TrackingUowFactory()
    function = _function(type=FunctionType.JOB)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        user_email="u@example.com",
        status=FunctionRunStatus.PENDING,
    )
    completed = run.model_copy()
    completed.status = FunctionRunStatus.COMPLETED
    dispatcher = SimpleNamespace(
        execute=AsyncMock(return_value=completed),
        cancel=AsyncMock(),
    )

    use_cases = FunctionUseCases(
        factory,
        lambda uow: SimpleNamespace(),
        SimpleNamespace(),
        dispatcher,
        AsyncMock(),
    )
    # No request / no ctx is supplied — the worker trusts the persisted run.
    result = await use_cases.execute_run_by_id(run.id)

    dispatcher.execute.assert_awaited_once_with(
        run.id,
        mode=FunctionDispatchMode.ASYNCHRONOUS,
    )
    assert result.status == FunctionRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_job_returns_pending_without_running_sandbox():
    factory = _TrackingUowFactory()
    function = _function(type=FunctionType.JOB)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        status=FunctionRunStatus.PENDING,
    )
    run.job_id = f"function:{run.id}"
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            return_value=ResolvedExecution(function=function, run=run)
        ),
    )
    dispatcher = SimpleNamespace(execute=AsyncMock(), cancel=AsyncMock())
    queue = SimpleNamespace(enqueue=AsyncMock(return_value=f"function:{run.id}"))

    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        SimpleNamespace(),
        dispatcher,
        queue,
    )
    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name="job-fn",
        input_data={},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    # The run transaction, including its deterministic async dispatch identity,
    # closes before the one queue round-trip.
    assert result.status == FunctionRunStatus.PENDING
    assert result.job_id == f"function:{run.id}"
    queue.enqueue.assert_awaited_once_with(run.id)
    assert factory.state["opens"] == 1
    dispatcher.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_job_queue_failure_leaves_recoverable_pending_run():
    factory = _TrackingUowFactory()
    function = _function(type=FunctionType.JOB)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        status=FunctionRunStatus.PENDING,
        job_id=None,
    )
    run.job_id = f"function:{run.id}"
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            return_value=ResolvedExecution(function=function, run=run)
        ),
    )
    dispatcher = SimpleNamespace(execute=AsyncMock(), cancel=AsyncMock())
    queue = SimpleNamespace(
        enqueue=AsyncMock(side_effect=FunctionRunQueueUnavailable("down"))
    )
    use_cases = FunctionUseCases(
        factory,
        lambda uow: service,
        SimpleNamespace(),
        dispatcher,
        queue,
    )

    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name="job-fn",
        input_data={},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    assert result.status == FunctionRunStatus.PENDING
    assert result.job_id == f"function:{run.id}"
    assert factory.state["opens"] == 1
    dispatcher.execute.assert_not_awaited()
