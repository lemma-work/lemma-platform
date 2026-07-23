"""Real backend -> AgentBox -> Docker runner -> backend execution contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from agentbox_client import AgentBoxClient, WorkloadKind

from app.core.config import reveal_secret, settings
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.function.api.dependencies import get_function_storage_factory
from app.modules.function.application.function_artifact_builder import (
    FunctionArtifactBuilder,
)
from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.domain.entities import (
    FunctionExecutionStatus,
    FunctionRevisionStatus,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.infrastructure.models import (
    FunctionExecutionRequestModel,
    FunctionModel,
    FunctionRevisionModel,
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


async def _create_run(
    session,
    *,
    pod_id: UUID,
    user_id: UUID,
    kind: FunctionType,
    value: int,
) -> UUID:
    function_id = uuid7()
    function = FunctionModel(
        id=function_id,
        pod_id=pod_id,
        user_id=user_id,
        name=f"docker-{kind.value.lower()}-{function_id.hex[:8]}",
        input_schema={},
        output_schema={},
        type=kind,
        status=FunctionStatus.READY,
        visibility="POD",
        python_packages=[],
    )
    session.add(function)
    await session.flush()

    revision = await FunctionArtifactBuilder(get_function_storage_factory()).build(
        function_id=function_id,
        revision_number=1,
        code=_CODE,
        python_packages=(),
    )
    session.add(
        FunctionRevisionModel(
            id=revision.id,
            function_id=function_id,
            revision_number=1,
            status=FunctionRevisionStatus.READY,
            code_sha256=revision.code_sha256,
            artifact_sha256=revision.artifact_sha256,
            artifact_path=revision.artifact_path,
            runtime_abi=revision.runtime_abi,
            builder_digest=revision.builder_digest,
            dependency_lock=list(revision.dependency_lock),
            manifest=revision.manifest,
            idempotent=False,
        )
    )
    function.active_revision_id = revision.id
    run_id = uuid7()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=45)
    session.add(
        FunctionRunModel(
            id=run_id,
            function_id=function_id,
            revision_id=revision.id,
            user_id=user_id,
            input_data={"value": value},
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
            revision_id=revision.id,
            kind=kind.value,
            status=FunctionExecutionStatus.QUEUED.value,
            priority=0 if kind == FunctionType.API else 10,
            units=2,
            next_fence=1,
            available_at=datetime.now(timezone.utc),
            deadline_at=deadline,
        )
    )
    await session.commit()
    return run_id


@pytest.mark.asyncio
async def test_api_and_job_execute_through_one_per_pod_docker_sandbox(
    local_agentbox_server,
    backend_server,
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
    settings.function_runtime_gateway_url = backend_server["docker_base_url"]
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
        secret = reveal_secret(settings.function_runtime_secret)
        assert secret is not None

        def client_factory() -> AgentBoxClient:
            return AgentBoxClient(
                base_url=local_agentbox_server["manager_base_url"],
                api_key=local_agentbox_server["api_key"],
                timeout_seconds=60,
            )

        dispatcher = FunctionDispatcher(
            uow_factory=SessionUnitOfWorkFactory(db_manager.session_factory),
            credential_signer=FunctionAttemptCredentialSigner(secret),
            agentbox_client_factory=client_factory,
            worker_id="docker-e2e-dispatcher",
        )

        api_result = await dispatcher.execute(api_run_id)
        assert api_result.status == FunctionRunStatus.COMPLETED, api_result.error
        assert api_result.output_data == {"result": 40}
        async with client_factory() as client:
            first = await client.inspect_sandbox(WorkloadKind.FUNCTION, pod_id)
        assert first is not None and first.ready

        job_result = await dispatcher.execute(job_run_id)
        assert job_result.status == FunctionRunStatus.COMPLETED, job_result.error
        assert job_result.output_data == {"result": 42}
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
        settings.function_runtime_gateway_url = original_gateway_url
