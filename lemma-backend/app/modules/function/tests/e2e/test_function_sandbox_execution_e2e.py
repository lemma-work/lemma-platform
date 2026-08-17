"""Real backend -> sandbox -> Docker runner -> backend execution contract."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from sandbox_runtime.protocol import WorkloadKind

from app.modules.workspace.services.local_sandbox_client import (
    LocalSandboxClient,
)

from app.core.config import settings
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.composition.workspace_identity import (
    mint_function_session_token,
    resolve_workspace_organization_id,
)
from app.modules.function.api.dependencies import get_function_storage_factory
from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.application.function_runtime_endpoint_cache import (
    FunctionRuntimeEndpointCache,
)
from app.modules.function.application.function_runtime_http_client import (
    FunctionRuntimeHttpClientPool,
)
from app.modules.function.application.function_session_token_cache import (
    FunctionSessionTokenCache,
)
from app.modules.function.domain.entities import (
    FunctionDispatchMode,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import (
    FunctionModel,
    FunctionRunModel,
)


pytestmark = [pytest.mark.e2e, pytest.mark.real_sandbox]


_CODE = """# input_type_name: Input
# output_type_name: Output
# function_name: execute
from pydantic import BaseModel

class Input(BaseModel):
    value: int

class Output(BaseModel):
    result: int

async def execute(context, data: Input) -> Output:
    assert context.pod_id is not None
    return Output(result=data.value * 2)
"""

_COMPILED_DEPENDENCY_CODE = """# input_type_name: Input
# output_type_name: Output
# function_name: execute
# python_packages: orjson>=3.11,<4
import orjson
from pydantic import BaseModel

class Input(BaseModel):
    value: int

class Output(BaseModel):
    result: int

async def execute(context, data: Input) -> Output:
    encoded = orjson.dumps({"value": data.value})
    decoded = orjson.loads(encoded)
    return Output(result=decoded["value"] * 3)
"""


async def _create_run(
    session,
    *,
    pod_id: UUID,
    user_id: UUID,
    kind: FunctionType,
    value: int,
    code: str = _CODE,
    python_packages: tuple[str, ...] = (),
) -> UUID:
    function_id = uuid7()
    artifact = await FunctionArtifactBuilder(get_function_storage_factory()).build(
        function_id=function_id,
        code=code,
        python_packages=python_packages,
    )
    function = FunctionModel(
        id=function_id,
        pod_id=pod_id,
        user_id=user_id,
        # UUIDv7 values created in one millisecond share their leading bytes.
        # Use the random suffix so multiple same-kind functions never collide.
        name=f"docker-{kind.value.lower()}-{function_id.hex[-12:]}",
        input_schema={},
        output_schema={},
        type=kind,
        status=FunctionStatus.READY,
        visibility="POD",
        revision_hash=artifact.revision_hash,
    )
    session.add(function)
    await session.flush()

    run_id = uuid7()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=45)
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_hash=artifact.revision_hash,
            user_id=user_id,
            input_data={"value": value},
            status=FunctionRunStatus.PENDING,
            job_id=f"function-run:{run_id}" if kind == FunctionType.JOB else None,
            deadline_at=deadline,
        )
    )
    await session.commit()
    return run_id


async def _wait_for_terminal(db_manager, run_id: UUID) -> FunctionRunModel:
    deadline = asyncio.get_running_loop().time() + 45
    while asyncio.get_running_loop().time() < deadline:
        async with db_manager.session_factory() as session:
            run = await session.get(FunctionRunModel, run_id)
            if run is not None and run.status in {
                FunctionRunStatus.COMPLETED,
                FunctionRunStatus.FAILED,
                FunctionRunStatus.CANCELLED,
            }:
                return run
        await asyncio.sleep(0.05)
    raise AssertionError(f"function run {run_id} did not become terminal")


@pytest.mark.asyncio
async def test_api_and_job_execute_through_one_per_pod_docker_sandbox(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    db_manager,
    db_session,
    test_pod,
    fixed_test_user,
    e2e_settings,
) -> None:
    del e2e_settings
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    original_gateway_url = settings.function_runtime_gateway_url
    # The URL the *sandbox* uses to fetch its artifact, which is not always the
    # one this process would use. A local container reaches the host gateway; a
    # sandbox in E2B's cloud needs a publicly resolvable address, and the
    # fixture publishes whichever applies (starting a tunnel when it must).
    settings.function_runtime_gateway_url = configure_workspace_api_url[
        "workspace_callback_url"
    ]
    try:
        api_run_id = await _create_run(
            db_session,
            pod_id=pod_id,
            user_id=user_id,
            kind=FunctionType.API,
            value=20,
        )
        job_run_id = await _create_run(
            db_session,
            pod_id=pod_id,
            user_id=user_id,
            kind=FunctionType.JOB,
            value=21,
        )
        def client_factory() -> LocalSandboxClient:
            from app.modules.workspace.services.sandbox_composition import (
                build_local_client,
            )

            return build_local_client()

        runtime_http_clients = FunctionRuntimeHttpClientPool()
        dispatcher = FunctionDispatcher(
            uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            sandbox_client_factory=client_factory,
            token_minter=mint_function_session_token,
            token_cache=FunctionSessionTokenCache(),
            endpoint_cache=FunctionRuntimeEndpointCache(),
            runtime_http_client_factory=runtime_http_clients.get,
            organization_resolver=resolve_workspace_organization_id,
            delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
        )

        api_result = await dispatcher.execute(
            api_run_id,
            mode=FunctionDispatchMode.SYNCHRONOUS,
        )
        assert api_result.status == FunctionRunStatus.COMPLETED, api_result.error
        assert api_result.output_data == {"result": 40}
        async with client_factory() as client:
            first = await client.inspect_sandbox(WorkloadKind.FUNCTION, pod_id)
        assert first is not None and first.ready

        job_accepted = await dispatcher.execute(
            job_run_id,
            mode=FunctionDispatchMode.ASYNCHRONOUS,
        )
        assert job_accepted.status in {
            FunctionRunStatus.RUNNING,
            FunctionRunStatus.COMPLETED,
        }
        job_result = (await _wait_for_terminal(db_manager, job_run_id)).to_entity()
        assert job_result.status == FunctionRunStatus.COMPLETED, job_result.error
        assert job_result.output_data == {"result": 42}

        compiled_run_id = await _create_run(
            db_session,
            pod_id=pod_id,
            user_id=user_id,
            kind=FunctionType.API,
            value=14,
            code=_COMPILED_DEPENDENCY_CODE,
            python_packages=("orjson>=3.11,<4",),
        )
        compiled_result = await dispatcher.execute(
            compiled_run_id,
            mode=FunctionDispatchMode.SYNCHRONOUS,
        )
        assert compiled_result.status == FunctionRunStatus.COMPLETED, (
            compiled_result.error
        )
        assert compiled_result.output_data == {"result": 42}
        async with client_factory() as client:
            second = await client.inspect_sandbox(WorkloadKind.FUNCTION, pod_id)
            assert second is not None and second.ready
            assert second.allocation_id == first.allocation_id
            await client.destroy_sandbox(
                WorkloadKind.FUNCTION,
                pod_id,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15),
            )
    finally:
        if "runtime_http_clients" in locals():
            await runtime_http_clients.close()
        settings.function_runtime_gateway_url = original_gateway_url


@pytest.mark.asyncio
async def test_a_destroyed_sandbox_behind_a_warm_endpoint_costs_no_failed_run(
    local_sandbox_server,
    backend_server,
    configure_workspace_api_url,
    db_manager,
    db_session,
    test_pod,
    fixed_test_user,
    e2e_settings,
) -> None:
    """The residual from the 2026-08-16 P0, closed.

    Quarantine turned a permanent outage into a single failed run: the run that
    discovered the dead endpoint still failed, because `InvocationOutcomeUnconfirmed`
    is never replayed. That is the right default -- a replayed function can
    write its side effects twice -- but it is too strict for one case. When the
    connection is *refused*, the request was never delivered and nothing ran, so
    re-resolving and retrying is provably safe.

    Reproduced the way production reached it: a warm cached endpoint pointing at
    a sandbox that no longer exists. The dispatcher keeps its endpoint cache
    across runs and re-acquires warm, so the second run dials the dead address
    rather than resolving a fresh one.
    """
    del e2e_settings
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    original_gateway_url = settings.function_runtime_gateway_url
    settings.function_runtime_gateway_url = configure_workspace_api_url[
        "workspace_callback_url"
    ]

    def client_factory() -> LocalSandboxClient:
        from app.modules.workspace.services.sandbox_composition import (
            build_local_client,
        )

        return build_local_client()

    runtime_http_clients = FunctionRuntimeHttpClientPool()
    try:
        # One dispatcher for both runs: the endpoint cache is what makes the
        # second run reuse the address of a sandbox that is already gone.
        dispatcher = FunctionDispatcher(
            uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            sandbox_client_factory=client_factory,
            token_minter=mint_function_session_token,
            token_cache=FunctionSessionTokenCache(),
            endpoint_cache=FunctionRuntimeEndpointCache(),
            runtime_http_client_factory=runtime_http_clients.get,
            organization_resolver=resolve_workspace_organization_id,
            delegated_tokens_enabled=settings.authz_delegated_tokens_enabled,
        )

        warm_run_id = await _create_run(
            db_session, pod_id=pod_id, user_id=user_id, kind=FunctionType.API, value=20
        )
        warm = await dispatcher.execute(
            warm_run_id, mode=FunctionDispatchMode.SYNCHRONOUS
        )
        assert warm.status == FunctionRunStatus.COMPLETED, warm.error

        async with client_factory() as client:
            before = await client.inspect_sandbox(WorkloadKind.FUNCTION, pod_id)
            assert before is not None and before.ready
            await client.destroy_sandbox(
                WorkloadKind.FUNCTION,
                pod_id,
                deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15),
            )

        after_run_id = await _create_run(
            db_session, pod_id=pod_id, user_id=user_id, kind=FunctionType.API, value=21
        )
        recovered = await dispatcher.execute(
            after_run_id, mode=FunctionDispatchMode.SYNCHRONOUS
        )

        # Before this change the run above failed with "response was lost" and
        # only the *next* one succeeded.
        assert recovered.status == FunctionRunStatus.COMPLETED, recovered.error
        assert recovered.output_data == {"result": 42}

        async with client_factory() as client:
            replacement = await client.inspect_sandbox(WorkloadKind.FUNCTION, pod_id)
        assert replacement is not None and replacement.ready
        # The sandbox is one-per-pod, so its logical id is stable across
        # replacement and cannot show this. The epoch is the fence -- a name
        # built from epoch 1 cannot resolve once the sandbox has moved to 2 --
        # so a bumped epoch is what proves the retry ran somewhere new.
        assert replacement.allocation_epoch > before.allocation_epoch, (
            "the retry landed on the sandbox that was just destroyed"
        )
    finally:
        await runtime_http_clients.close()
        settings.function_runtime_gateway_url = original_gateway_url
        async with client_factory() as client:
            with contextlib.suppress(Exception):
                await client.destroy_sandbox(
                    WorkloadKind.FUNCTION,
                    pod_id,
                    deadline_at=datetime.now(timezone.utc) + timedelta(seconds=15),
                )
