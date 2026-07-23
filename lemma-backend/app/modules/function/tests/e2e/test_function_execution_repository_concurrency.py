from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest
from sqlalchemy import func, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.domain.entities import (
    FunctionExecutionStatus,
    FunctionRevisionStatus,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.models import (
    FunctionExecutionAttemptModel,
    FunctionExecutionRequestModel,
    FunctionModel,
    FunctionRevisionModel,
    FunctionRunModel,
)


pytestmark = pytest.mark.e2e


async def _seed_queued_run(session, *, pod_id: UUID, user_id: UUID) -> UUID:
    function_id = uuid7()
    revision_id = uuid7()
    run_id = uuid7()
    deadline = datetime.now(timezone.utc) + timedelta(minutes=1)
    function = FunctionModel(
        id=function_id,
        pod_id=pod_id,
        user_id=user_id,
        name=f"claim-race-{function_id.hex[:8]}",
        input_schema={},
        output_schema={},
        type=FunctionType.API,
        status=FunctionStatus.READY,
        visibility="POD",
        python_packages=[],
        active_revision_id=revision_id,
    )
    session.add(function)
    await session.flush()
    session.add(
        FunctionRevisionModel(
            id=revision_id,
            function_id=function_id,
            revision_number=1,
            status=FunctionRevisionStatus.READY,
            code_sha256="sha256:" + "1" * 64,
            artifact_sha256="sha256:" + "2" * 64,
            artifact_path=f"test/{revision_id}.zip",
            runtime_abi="test",
            builder_digest="test",
            dependency_lock=[],
            manifest={},
            idempotent=False,
        )
    )
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_id=revision_id,
            user_id=user_id,
            input_data={},
            status=FunctionRunStatus.PENDING,
            execution_fence=0,
            deadline_at=deadline,
        )
    )
    await session.flush()
    session.add(
        FunctionExecutionRequestModel(
            run_id=run_id,
            pod_id=pod_id,
            function_id=function_id,
            revision_id=revision_id,
            kind=FunctionType.API.value,
            status=FunctionExecutionStatus.QUEUED.value,
            priority=0,
            units=2,
            next_fence=1,
            available_at=datetime.now(timezone.utc),
            deadline_at=deadline,
        )
    )
    await session.commit()
    return run_id


async def test_claim_refreshes_request_loaded_before_a_competing_commit(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as setup_session:
        run_id = await _seed_queued_run(
            setup_session,
            pod_id=pod_id,
            user_id=user_id,
        )

    signer = FunctionAttemptCredentialSigner("claim-race-secret-32-bytes-long!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with db_manager.session_factory() as stale_session:
        stale_request = await stale_session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == run_id
            )
        )
        assert stale_request is not None
        assert stale_request.status == FunctionExecutionStatus.QUEUED.value
        assert stale_request.next_fence == 1

        async with factory() as winner_uow:
            winner = await FunctionExecutionRepository(winner_uow, signer).claim_run(
                run_id,
                worker_id="winner",
                total_units=8,
                api_reserved_units=2,
                lease_seconds=30,
            )
        assert winner is not None
        assert winner.fence == 1

        # The losing session still has the pre-commit ORM object cached. The
        # repository must reload it after acquiring the pod admission lock,
        # otherwise it will create a second attempt with fence 1.
        loser = await FunctionExecutionRepository(
            SqlAlchemyUnitOfWork(stale_session), signer
        ).claim_run(
            run_id,
            worker_id="loser",
            total_units=8,
            api_reserved_units=2,
            lease_seconds=30,
        )
        assert loser is None
        await stale_session.rollback()

    async with db_manager.session_factory() as verification_session:
        attempt_count = await verification_session.scalar(
            select(func.count(FunctionExecutionAttemptModel.id)).where(
                FunctionExecutionAttemptModel.run_id == run_id
            )
        )
        request = await verification_session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == run_id
            )
        )
    assert attempt_count == 1
    assert request is not None
    assert request.next_fence == 2


async def test_expired_dispatcher_lease_reclaims_same_fenced_attempt(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    async with db_manager.session_factory() as setup_session:
        run_id = await _seed_queued_run(
            setup_session,
            pod_id=pod_id,
            user_id=user_id,
        )

    signer = FunctionAttemptCredentialSigner("restart-recovery-secret-32-bytes!!")
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    claimed_at = datetime.now(timezone.utc)
    async with factory() as first_uow:
        first = await FunctionExecutionRepository(first_uow, signer).claim_run(
            run_id,
            worker_id="worker-before-restart",
            total_units=8,
            api_reserved_units=2,
            lease_seconds=10,
            now=claimed_at,
        )
    assert first is not None

    async with factory() as restarted_uow:
        reclaimed = await FunctionExecutionRepository(restarted_uow, signer).claim_run(
            run_id,
            worker_id="worker-after-restart",
            total_units=8,
            api_reserved_units=2,
            lease_seconds=10,
            now=claimed_at + timedelta(seconds=11),
        )
    assert reclaimed is not None
    assert reclaimed.attempt_id == first.attempt_id
    assert reclaimed.operation_id == first.operation_id
    assert reclaimed.fence == first.fence
    assert reclaimed.ticket == first.ticket
    assert reclaimed.runtime_token == first.runtime_token

    async with db_manager.session_factory() as verification_session:
        attempts = (
            await verification_session.scalars(
                select(FunctionExecutionAttemptModel).where(
                    FunctionExecutionAttemptModel.run_id == run_id
                )
            )
        ).all()
        request = await verification_session.scalar(
            select(FunctionExecutionRequestModel).where(
                FunctionExecutionRequestModel.run_id == run_id
            )
        )
    assert len(attempts) == 1
    assert request is not None
    assert request.next_fence == 2
    assert request.lease_owner == "worker-after-restart"
