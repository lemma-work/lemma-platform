"""A burst of function runs against one pod: queueing, deadlines and blast radius.

Each test drives the real Postgres the behaviour depends on; the sandbox side
is replaced at its HTTP and provider seams only. The pod gate and the deadline
item are covered with the work runtime in ``test_function_run_work_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import httpx
import pytest
from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpoint,
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_quarantine import (
    ORPHANED_RUN_ERROR,
    FunctionRuntimeQuarantine,
)
from app.modules.function.application.function_runtime_route_resolver import (
    FunctionRuntimeRouteResolver,
)
from app.modules.function.config import function_settings
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionExecutionDispatch,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.execution_repository import (
    FunctionExecutionRepository,
)
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.workspace.domain.sandbox import (
    SandboxInstanceState,
    SandboxKind,
    SandboxOwnerKind,
)
from app.modules.workspace.infrastructure.sandbox_repository import SandboxRepository
from app.modules.workspace.providers import naming
from app.modules.workspace.services.local_sandbox_client import LocalSandboxClient
from app.modules.workspace.services.sandbox_service import SandboxService


pytestmark = pytest.mark.e2e
_REVISION_HASH = f"sha256:{'3' * 64}"


async def _seed_function(session, *, pod_id: UUID, user_id: UUID, job: bool) -> UUID:
    function_id = uuid7()
    session.add(
        FunctionModel(
            id=function_id,
            pod_id=pod_id,
            user_id=user_id,
            name=f"fanout-{function_id.hex}",
            input_schema={},
            output_schema={},
            type=FunctionType.JOB if job else FunctionType.API,
            status=FunctionStatus.READY,
            visibility="POD",
            revision_hash=_REVISION_HASH,
        )
    )
    await session.flush()
    return function_id


async def _seed_run(
    session,
    *,
    function_id: UUID,
    user_id: UUID,
    deadline_at: datetime,
    job: bool,
    status: FunctionRunStatus = FunctionRunStatus.PENDING,
    started_at: datetime | None = None,
) -> UUID:
    run_id = uuid7()
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_hash=_REVISION_HASH,
            user_id=user_id,
            input_data={"value": 1},
            status=status,
            job_id=f"function-run:{run_id}" if job else None,
            deadline_at=deadline_at,
            started_at=started_at,
        )
    )
    await session.flush()
    return run_id


async def _status_of(db_manager, run_ids: list[UUID]) -> dict[UUID, FunctionRunModel]:
    async with db_manager.session_factory() as session:
        rows = (
            await session.scalars(
                select(FunctionRunModel).where(FunctionRunModel.id.in_(run_ids))
            )
        ).all()
        return {row.id: row for row in rows}


async def test_execution_deadline_starts_when_the_run_starts_not_when_it_was_queued(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """A run that waited in a burst keeps its whole execution budget."""
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    created = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        function_id = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=False
        )
        run_id = await _seed_run(
            session,
            function_id=function_id,
            user_id=user_id,
            deadline_at=created + timedelta(minutes=30),
            job=True,
        )
        await session.commit()

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        dispatch = await FunctionExecutionRepository(uow).resolve_dispatch(
            run_id, mode=FunctionDispatchMode.ASYNCHRONOUS
        )
    assert isinstance(dispatch, FunctionExecutionDispatch)
    budget = function_settings.function_api_deadline_seconds
    # Started well after an API function's whole budget had passed in the queue.
    started = created + timedelta(seconds=budget + 300)
    async with factory() as uow:
        context = await FunctionExecutionRepository(uow).start_execution(
            dispatch, now=started
        )

    assert context is not None
    assert context.deadline_at == started + timedelta(seconds=budget)
    # Still inside its budget from the start, so its deadline leaves it alone.
    async with factory() as uow:
        later = await FunctionExecutionRepository(uow).fail_if_overdue(
            run_id, now=started + timedelta(seconds=budget - 1), grace=timedelta(0)
        )
    assert later == started + timedelta(seconds=budget)
    row = (await _status_of(db_manager, [run_id]))[run_id]
    assert row.status == FunctionRunStatus.RUNNING
    assert row.deadline_at == started + timedelta(seconds=budget)


class _Provider:
    """The provider seam under the real sandbox service: records destroys.

    Below the service rather than in place of the client, so the epoch fence
    that decides whether anything is destroyed is the shipping one.
    """

    name = "fanout-test"
    destroyed: list[str]

    def __init__(self) -> None:
        self.destroyed = []

    async def destroy(self, name: str, *, deadline_at: datetime) -> None:
        del deadline_at
        self.destroyed.append(name)

    async def close(self) -> None:
        return None


def _routes(db_manager, provider: _Provider) -> FunctionRuntimeRouteResolver:
    service = SandboxService(
        provider=provider,
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
    )
    return FunctionRuntimeRouteResolver(
        sandbox_client_factory=lambda: LocalSandboxClient(service),
        endpoint_cache=FunctionRuntimeEndpointCache(),
    )


async def _seed_function_sandbox(db_manager, pod_id: UUID, *, epochs: int) -> list[str]:
    """The pod's function sandbox, provisioned ``epochs`` times; its names."""
    names: list[str] = []
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        repository = SandboxRepository(uow)
        await repository.ensure_row(
            sandbox_id=pod_id,
            kind=SandboxKind.FUNCTION,
            owner_kind=SandboxOwnerKind.POD,
            owner_id=pod_id,
        )
        for epoch in range(1, epochs + 1):
            if epoch > 1:
                await repository.bump_epoch(pod_id)
            name = naming.container_name(pod_id, SandboxKind.FUNCTION, epoch)
            instance = await repository.begin_instance(
                sandbox_id=pod_id,
                provider=_Provider.name,
                provider_id=name,
                provider_volume_id=None,
                epoch=epoch,
            )
            await repository.mark_instance_ready(instance.id)
            names.append(name)
    return names


def _runtime_answering_probe(status_code: int) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/healthz")
        return httpx.Response(status_code, json={"ready": status_code == 200})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _endpoint(*, epoch: int = 1) -> FunctionRuntimeEndpoint:
    return FunctionRuntimeEndpoint(
        url="https://sandbox.example.test/runtime/",
        request_headers=(),
        allocation_id=uuid4(),
        allocation_epoch=epoch,
        profile_digest=f"sha256:{'2' * 64}",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


async def _seed_in_flight(db_manager, *, pod_id: UUID, user_id: UUID):
    now = datetime.now(timezone.utc)
    async with db_manager.session_factory() as session:
        job_function = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=True
        )
        api_function = await _seed_function(
            session, pod_id=pod_id, user_id=user_id, job=False
        )
        started = now - timedelta(seconds=5)
        deadline = now + timedelta(minutes=10)
        running = FunctionRunStatus.RUNNING
        jobs = [
            await _seed_run(
                session,
                function_id=job_function,
                job=True,
                user_id=user_id,
                status=running,
                started_at=started,
                deadline_at=deadline,
            )
            for _ in range(3)
        ]
        synchronous = await _seed_run(
            session,
            function_id=api_function,
            job=False,
            user_id=user_id,
            status=running,
            started_at=started,
            deadline_at=deadline,
        )
        await session.commit()
    return jobs, synchronous


async def test_a_5xx_on_a_live_runtime_leaves_the_sandbox_and_its_runs_alone(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    pod_id = UUID(test_pod["id"])
    jobs, _ = await _seed_in_flight(
        db_manager, pod_id=pod_id, user_id=UUID(fixed_test_user["id"])
    )
    provider = _Provider()
    quarantine = FunctionRuntimeQuarantine(
        routes=_routes(db_manager, provider),
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        runtime_http_client_factory=lambda: probe,
    )

    async with _runtime_answering_probe(200) as probe:
        quarantined = await quarantine.after_failure(pod_id, _endpoint())

    assert quarantined is False
    assert provider.destroyed == []
    rows = await _status_of(db_manager, jobs)
    assert {row.status for row in rows.values()} == {FunctionRunStatus.RUNNING}


async def test_a_dead_runtime_is_replaced_and_its_job_runs_fail_at_once(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """Nothing will ever report for those runs; they fail now, not at their
    deadline, and so stop holding their pod's slots."""
    pod_id = UUID(test_pod["id"])
    jobs, synchronous = await _seed_in_flight(
        db_manager, pod_id=pod_id, user_id=UUID(fixed_test_user["id"])
    )
    (sandbox,) = await _seed_function_sandbox(db_manager, pod_id, epochs=1)
    provider = _Provider()
    quarantine = FunctionRuntimeQuarantine(
        routes=_routes(db_manager, provider),
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        runtime_http_client_factory=lambda: probe,
    )

    async with _runtime_answering_probe(502) as probe:
        quarantined = await quarantine.after_failure(pod_id, _endpoint())

    assert quarantined is True
    assert provider.destroyed == [sandbox]
    rows = await _status_of(db_manager, [*jobs, synchronous])
    for run_id in jobs:
        assert rows[run_id].status == FunctionRunStatus.FAILED
        assert rows[run_id].error == ORPHANED_RUN_ERROR
    # A synchronous run holds its own connection and fails through it.
    assert rows[synchronous].status == FunctionRunStatus.RUNNING


async def test_a_late_quarantine_of_a_replaced_sandbox_destroys_nothing(
    db_manager,
    test_pod,
    fixed_test_user,
) -> None:
    """Two runs fail on one dead runtime; the first replaces it.

    The second still holds the endpoint leased from the dead sandbox. Its
    quarantine must not destroy the replacement, nor fail the runs now in
    flight on it -- they are healthy, and nothing is wrong with their sandbox.
    """
    pod_id = UUID(test_pod["id"])
    jobs, _ = await _seed_in_flight(
        db_manager, pod_id=pod_id, user_id=UUID(fixed_test_user["id"])
    )
    dead, replacement = await _seed_function_sandbox(db_manager, pod_id, epochs=2)
    provider = _Provider()
    quarantine = FunctionRuntimeQuarantine(
        routes=_routes(db_manager, provider),
        uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
        runtime_http_client_factory=lambda: probe,
    )

    async with _runtime_answering_probe(502) as probe:
        await quarantine.after_failure(pod_id, _endpoint(epoch=1))

    assert provider.destroyed == []
    rows = await _status_of(db_manager, jobs)
    assert {row.status for row in rows.values()} == {FunctionRunStatus.RUNNING}
    async with SessionUnitOfWorkFactory(db_manager.session_factory)() as uow:
        current = await SandboxRepository(uow).current_instance(pod_id)
    assert current is not None
    assert current.provider_id == replacement
    assert current.state is not SandboxInstanceState.DESTROYED
    assert dead != replacement
