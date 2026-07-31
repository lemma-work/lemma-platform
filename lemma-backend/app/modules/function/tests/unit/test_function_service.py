"""Database-phase tests for the canonical function application service."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization.context import Context
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.domain.errors import (
    FunctionConflictError,
    FunctionNotFoundError,
    FunctionRunNotFoundError,
    FunctionValidationError,
)
from app.modules.function.services.function_service import (
    FunctionService,
    LegacyFunctionRevisionRequired,
    parse_python_packages,
)
from app.modules.test_support.authz import allow_all_context, deny_all_context


pytestmark = pytest.mark.asyncio


def _function(**overrides) -> FunctionEntity:
    values = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "test-function",
        "status": FunctionStatus.DRAFT,
    }
    values.update(overrides)
    return FunctionEntity(**values)


def _run(function: FunctionEntity) -> FunctionRunEntity:
    assert function.id is not None
    return FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        input_data={},
        status=FunctionRunStatus.PENDING,
    )


@pytest.fixture
def context() -> Context:
    return allow_all_context()


@pytest.fixture
def function_repository() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def run_repository() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(
    function_repository: AsyncMock,
    run_repository: AsyncMock,
) -> FunctionService:
    return FunctionService(
        function_repository=function_repository,
        run_repository=run_repository,
        storage_factory=lambda _function_id: AsyncMock(),
    )


async def test_resolve_create_is_database_only(
    service: FunctionService,
    function_repository: AsyncMock,
    context: Context,
) -> None:
    entity = _function(id=None, name="new-function")
    created = entity.model_copy(update={"id": uuid4()})
    function_repository.get_by_name.return_value = None
    function_repository.create.return_value = created

    result = await service.resolve_create(entity, entity.user_id, ctx=context)

    assert result == created


async def test_resolve_create_rejects_duplicate_name(
    service: FunctionService,
    function_repository: AsyncMock,
    context: Context,
) -> None:
    entity = _function(id=None)
    function_repository.get_by_name.return_value = _function(name=entity.name)

    with pytest.raises(FunctionConflictError):
        await service.resolve_create(entity, entity.user_id, ctx=context)


async def test_resolve_create_requires_authorization(service: FunctionService) -> None:
    entity = _function(id=None)
    with pytest.raises(Exception):
        await service.resolve_create(
            entity,
            entity.user_id,
            ctx=deny_all_context(),
        )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("#python_packages: pandas numpy pandas\n", ["pandas", "numpy"]),
        (
            "#python_packages: requests[socks] numpy>=1.0,<2.0\n",
            [
                "requests[socks]",
                "numpy>=1.0,<2.0",
            ],
        ),
        ("#function_name: execute\n", []),
    ],
)
async def test_parse_python_packages(header: str, expected: list[str]) -> None:
    assert parse_python_packages(header) == expected


@pytest.mark.parametrize(
    "requirement",
    ["--index-url=x", "../local", "https://example.test/pkg.whl", "name;bad"],
)
async def test_parse_python_packages_rejects_non_registry_specs(
    requirement: str,
) -> None:
    with pytest.raises(FunctionValidationError):
        parse_python_packages(f"#python_packages: {requirement}\n")


async def test_get_missing_function_can_be_strict(
    service: FunctionService,
    function_repository: AsyncMock,
    context: Context,
) -> None:
    function_repository.get_by_name.return_value = None
    with pytest.raises(FunctionNotFoundError):
        await service.get_function_by_name(
            uuid4(),
            "missing",
            uuid4(),
            raise_not_found=True,
            ctx=context,
        )


async def test_get_run_rejects_cross_function_id(
    service: FunctionService,
    function_repository: AsyncMock,
    run_repository: AsyncMock,
    context: Context,
) -> None:
    function = _function()
    run_repository.get_run.return_value = FunctionRunEntity(
        id=uuid4(),
        function_id=uuid4(),
        user_id=function.user_id,
    )
    function_repository.get_by_name.return_value = function

    with pytest.raises(FunctionValidationError, match="does not belong"):
        await service.get_run(
            function.pod_id,
            function.name,
            uuid4(),
            function.user_id,
            ctx=context,
        )


async def test_get_run_reports_missing(
    service: FunctionService,
    run_repository: AsyncMock,
) -> None:
    run_repository.get_run.return_value = None
    with pytest.raises(FunctionRunNotFoundError):
        await service.get_run(uuid4(), "fn", uuid4(), uuid4())


@pytest.mark.parametrize(
    ("function_type", "dispatch_mode", "expects_job_id"),
    [
        (FunctionType.API, None, False),
        (FunctionType.JOB, None, True),
        (FunctionType.API, FunctionDispatchMode.ASYNCHRONOUS, True),
    ],
)
async def test_resolve_execute_creates_only_the_durable_pending_run(
    service: FunctionService,
    function_repository: AsyncMock,
    run_repository: AsyncMock,
    context: Context,
    function_type: FunctionType,
    dispatch_mode: FunctionDispatchMode | None,
    expects_job_id: bool,
) -> None:
    function = _function(
        type=function_type,
        status=FunctionStatus.READY,
        revision_hash=f"sha256:{'2' * 64}",
    )
    function_repository.get_by_name.return_value = function
    run_repository.create_run.side_effect = lambda item: item

    resolved = await service.resolve_execute(
        function.pod_id,
        function.name,
        {"value": 1},
        function.user_id,
        None,
        ctx=context,
        dispatch_mode=dispatch_mode,
    )

    assert (resolved.run.job_id is not None) is expects_job_id
    assert resolved.run.collect_events() == []
    run_repository.create_run.assert_awaited_once()
    created_run = run_repository.create_run.await_args.args[0]
    assert created_run.revision_hash == function.revision_hash


async def test_resolve_execute_requires_ready_revision(
    service: FunctionService,
    function_repository: AsyncMock,
    context: Context,
) -> None:
    function = _function(status=FunctionStatus.DRAFT)
    function_repository.get_by_name.return_value = function

    with pytest.raises(FunctionValidationError, match="ready"):
        await service.resolve_execute(
            function.pod_id,
            function.name,
            {},
            function.user_id,
            None,
            ctx=context,
        )


async def test_resolve_execute_requests_backfill_for_ready_legacy_source(
    service: FunctionService,
    function_repository: AsyncMock,
    run_repository: AsyncMock,
    context: Context,
) -> None:
    function = _function(
        status=FunctionStatus.READY,
        code_path="test-function.py",
        revision_hash=None,
    )
    function_repository.get_by_name.return_value = function

    with pytest.raises(LegacyFunctionRevisionRequired) as raised:
        await service.resolve_execute(
            function.pod_id,
            function.name,
            {},
            function.user_id,
            None,
            ctx=context,
        )

    assert raised.value.function is function
    run_repository.create_run.assert_not_awaited()
