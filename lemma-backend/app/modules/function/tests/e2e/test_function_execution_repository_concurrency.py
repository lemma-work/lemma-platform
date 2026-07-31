from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenKey,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionSessionPrincipal,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.function.infrastructure.repositories import FunctionRunRepository


pytestmark = pytest.mark.e2e
_REVISION_HASH = f"sha256:{'2' * 64}"


async def _seed_run(
    session,
    *,
    pod_id: UUID,
    user_id: UUID,
    deadline_at: datetime | None = None,
    job: bool = False,
) -> UUID:
    function_id = uuid7()
    run_id = uuid7()
    session.add(
        FunctionModel(
            id=function_id,
            pod_id=pod_id,
            user_id=user_id,
            name=f"execution-race-{function_id.hex}",
            input_schema={},
            output_schema={},
            type=FunctionType.JOB if job else FunctionType.API,
            status=FunctionStatus.READY,
            visibility="POD",
            revision_hash=_REVISION_HASH,
        )
    )
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_hash=_REVISION_HASH,
            user_id=user_id,
            input_data={"value": 1},
            status=FunctionRunStatus.PENDING,
            job_id=f"function-run:{run_id}" if job else None,
            deadline_at=deadline_at
            or datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    )
    await session.commit()
    return run_id


def _principal(dispatch: FunctionExecutionDispatch) -> FunctionSessionPrincipal:
    key = FunctionSessionTokenKey(
        user_id=dispatch.user_id,
        pod_id=dispatch.pod_id,
        function_id=dispatch.function_id,
        revision_hash=dispatch.revision_hash,
        workload_name=dispatch.function_name,
        scope=(),
        delegated_tokens_enabled=True,
    )
    return FunctionSessionPrincipal(
        user_id=dispatch.user_id,
        pod_id=dispatch.pod_id,
        function_id=dispatch.function_id,
        session_id=key.session_id,
        actor_name=dispatch.function_name,
    )


async def _dispatch(factory, run_id: UUID) -> FunctionExecutionDispatch:
    async with factory() as uow:
        resolved = await FunctionExecutionRepository(uow).resolve_dispatch(
            run_id,
            mode=FunctionDispatchMode.SYNCHRONOUS,
        )
    assert isinstance(resolved, FunctionExecutionDispatch)
    return resolved


async def test_concurrent_backend_start_executes_one_public_run_once(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, run_id)

    async def start():
        async with factory() as uow:
            return await FunctionExecutionRepository(uow).start_execution(dispatch)

    starts = await asyncio.gather(start(), start())
    assert len([context for context in starts if context is not None]) == 1

    async with db_manager.session_factory() as session:
        run = await session.get(FunctionRunModel, run_id)
    assert run is not None
    assert run.status == FunctionRunStatus.RUNNING
    assert run.started_at is not None

    async with factory() as restarted_uow:
        restarted = await FunctionExecutionRepository(
            restarted_uow
        ).resolve_dispatch(
            run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )
    assert isinstance(restarted, FunctionRunEntity)
    assert restarted.status == FunctionRunStatus.RUNNING


async def test_terminal_requires_exact_standard_session_and_is_idempotent(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(
            session,
            pod_id=pod_id,
            user_id=user_id,
            job=True,
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, run_id)
    async with factory() as uow:
        started = await FunctionExecutionRepository(uow).start_execution(dispatch)
    assert started is not None

    wrong = _principal(dispatch).model_copy(
        update={"session_id": "function-session:wrong"}
    )
    async with factory() as uow:
        rejected = await FunctionExecutionRepository(
            uow
        ).authorized_runtime_context(
            run_id,
            wrong,
            delegated_tokens_enabled=True,
        )
    assert rejected is None

    async with factory() as uow:
        context = await FunctionExecutionRepository(
            uow
        ).authorized_runtime_context(
            run_id,
            _principal(dispatch),
            delegated_tokens_enabled=True,
        )
    assert context is not None

    async with factory() as uow:
        completed, accepted, duplicate = await FunctionExecutionRepository(
            uow
        ).complete(
            context,
            completed=True,
            output_data={"ok": True},
            error=None,
            logs=None,
        )
    assert completed is not None
    assert completed.status == FunctionRunStatus.COMPLETED
    assert accepted and not duplicate

    async with factory() as uow:
        duplicate_context = await FunctionExecutionRepository(
            uow
        ).authorized_runtime_context(
            run_id,
            _principal(dispatch),
            delegated_tokens_enabled=True,
        )
        assert duplicate_context is not None
        duplicate_run, accepted, duplicate = await FunctionExecutionRepository(
            uow
        ).complete(
            duplicate_context,
            completed=True,
            output_data={"ok": True},
            error=None,
            logs=None,
        )
    assert duplicate_run is not None
    assert accepted and duplicate


async def test_api_run_cannot_complete_through_job_callback_authorization(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, run_id)
    async with factory() as uow:
        started = await FunctionExecutionRepository(uow).start_execution(dispatch)
    assert started is not None

    async with factory() as uow:
        context = await FunctionExecutionRepository(
            uow
        ).authorized_runtime_context(
            run_id,
            _principal(dispatch),
            delegated_tokens_enabled=True,
        )
    assert context is None


async def test_expired_run_cannot_be_started(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(
            session,
            pod_id=pod_id,
            user_id=user_id,
            deadline_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, run_id)
    async with factory() as uow:
        context = await FunctionExecutionRepository(uow).start_execution(dispatch)
    assert context is None


async def test_artifact_authorization_is_exact_function_revision_session(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, run_id)
    principal = _principal(dispatch)
    async with factory() as uow:
        repository = FunctionExecutionRepository(uow)
        assert await repository.authorize_definition_artifact(
            dispatch.function_id,
            dispatch.revision_hash,
            principal,
            delegated_tokens_enabled=True,
        )
        assert not await repository.authorize_definition_artifact(
            dispatch.function_id,
            f"sha256:{'3' * 64}",
            principal,
            delegated_tokens_enabled=True,
        )


async def test_running_job_receives_callback_grace_before_reconciliation(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(
            session,
            pod_id=pod_id,
            user_id=user_id,
            deadline_at=deadline,
            job=True,
        )
        run = await session.get(FunctionRunModel, run_id)
        assert run is not None
        run.status = FunctionRunStatus.RUNNING
        await session.commit()

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        assert (
            await FunctionRunRepository(uow).fail_expired(
                now=deadline + timedelta(seconds=30),
                job_callback_grace_seconds=60,
            )
            == 0
        )
    async with factory() as uow:
        assert (
            await FunctionRunRepository(uow).fail_expired(
                now=deadline + timedelta(seconds=61),
                job_callback_grace_seconds=60,
            )
            == 1
        )
