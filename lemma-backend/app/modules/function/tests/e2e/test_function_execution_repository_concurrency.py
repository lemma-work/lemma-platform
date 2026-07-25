from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.application.function_runtime_credentials import (
    FunctionRuntimeCapabilitySigner,
)
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
) -> UUID:
    function_id = uuid7()
    run_id = uuid7()
    session.add(
        FunctionModel(
            id=function_id,
            pod_id=pod_id,
            user_id=user_id,
            name=f"claim-race-{function_id.hex}",
            input_schema={},
            output_schema={},
            type=FunctionType.API,
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
    )


async def _dispatch(factory, signer, run_id: UUID) -> FunctionExecutionDispatch:
    async with factory() as uow:
        resolved = await FunctionExecutionRepository(
            uow, signer
        ).resolve_dispatch(
            run_id,
            mode=FunctionDispatchMode.SYNCHRONOUS,
        )
    assert isinstance(resolved, FunctionExecutionDispatch)
    return resolved


async def test_concurrent_runtime_claim_executes_one_public_run_once(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    signer = FunctionRuntimeCapabilitySigner("claim-race-secret-32-bytes-long!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, signer, run_id)
    principal = _principal(dispatch)

    async def claim():
        async with factory() as uow:
            return await FunctionExecutionRepository(uow, signer).claim_execution(
                run_id,
                principal,
                revision_hash=dispatch.revision_hash,
                input_data=dispatch.input_data,
                delegated_tokens_enabled=True,
            )

    claims = await asyncio.gather(claim(), claim())
    accepted = [claim for claim in claims if claim is not None]
    assert len(accepted) == 1

    async with db_manager.session_factory() as session:
        run = await session.get(FunctionRunModel, run_id)
    assert run is not None
    assert run.status == FunctionRunStatus.RUNNING
    assert run.started_at is not None

    async with factory() as restarted_uow:
        restarted = await FunctionExecutionRepository(
            restarted_uow, signer
        ).resolve_dispatch(
            run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )
    assert isinstance(restarted, FunctionRunEntity)
    assert restarted.status == FunctionRunStatus.RUNNING


async def test_claim_requires_exact_delegated_session_and_terminal_is_idempotent(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    signer = FunctionRuntimeCapabilitySigner("run-claim-secret-32-bytes-long!!!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, signer, run_id)
    wrong = _principal(dispatch).model_copy(
        update={"session_id": "function-session:wrong"}
    )
    claim_arguments = {
        "revision_hash": dispatch.revision_hash,
        "input_data": dispatch.input_data,
        "delegated_tokens_enabled": True,
    }

    async with factory() as uow:
        rejected = await FunctionExecutionRepository(
            uow, signer
        ).claim_execution(run_id, wrong, **claim_arguments)
    assert rejected is None

    async with factory() as uow:
        context = await FunctionExecutionRepository(
            uow, signer
        ).claim_execution(
            run_id,
            _principal(dispatch),
            **claim_arguments,
        )
    assert context is not None

    async with factory() as uow:
        completed, accepted, duplicate = await FunctionExecutionRepository(
            uow, signer
        ).complete(
            context,
            completed=True,
            output_data={"ok": True},
            error=None,
            logs=None,
        )
    assert completed is not None
    assert completed.status == FunctionRunStatus.COMPLETED
    assert accepted is True
    assert duplicate is False

    async with factory() as uow:
        duplicate_context = await FunctionExecutionRepository(
            uow, signer
        ).runtime_context(run_id, signer.derive(run_id))
        assert duplicate_context is not None
        duplicate_run, accepted, duplicate = await FunctionExecutionRepository(
            uow, signer
        ).complete(
            duplicate_context,
            completed=True,
            output_data={"ok": True},
            error=None,
            logs=None,
        )
    assert duplicate_run is not None
    assert accepted is True
    assert duplicate is True


async def test_expired_run_cannot_be_claimed(
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

    signer = FunctionRuntimeCapabilitySigner("expired-run-secret-32-bytes-long!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, signer, run_id)
    async with factory() as uow:
        context = await FunctionExecutionRepository(uow, signer).claim_execution(
            run_id,
            _principal(dispatch),
            revision_hash=dispatch.revision_hash,
            input_data=dispatch.input_data,
            delegated_tokens_enabled=True,
        )
    assert context is None


async def test_artifact_capability_expires_with_the_running_run(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as session:
        run_id = await _seed_run(session, pod_id=pod_id, user_id=user_id)

    signer = FunctionRuntimeCapabilitySigner("artifact-secret-32-bytes-long!!!!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    dispatch = await _dispatch(factory, signer, run_id)
    async with factory() as uow:
        repository = FunctionExecutionRepository(uow, signer)
        claimed = await repository.claim_execution(
            run_id,
            _principal(dispatch),
            revision_hash=dispatch.revision_hash,
            input_data=dispatch.input_data,
            delegated_tokens_enabled=True,
        )
    assert claimed is not None

    callback_token = signer.derive(run_id)
    async with factory() as uow:
        repository = FunctionExecutionRepository(uow, signer)
        active = await repository.active_runtime_context(
            run_id,
            callback_token,
            now=dispatch.deadline_at - timedelta(microseconds=1),
        )
        expired = await repository.active_runtime_context(
            run_id,
            callback_token,
            now=dispatch.deadline_at,
        )

    assert active is not None
    assert expired is None

    async with factory() as uow:
        count = await FunctionRunRepository(uow).fail_expired(
            now=dispatch.deadline_at
        )
    assert count == 1
    async with db_manager.session_factory() as session:
        run = await session.get(FunctionRunModel, run_id)
    assert run is not None
    assert run.status == FunctionRunStatus.FAILED
    assert run.error == "Function execution deadline exceeded"
