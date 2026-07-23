from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest

from agentbox_client import ProcessRef, ProcessState, SandboxHandle
from agentbox_client.models import ProcessOutputSnapshot, ProfileRef, WorkloadKind

from app.modules.function.application.function_attempt_credentials import (
    FunctionAttemptCredentialSigner,
)
from app.modules.function.application.function_dispatcher import FunctionDispatcher
from app.modules.function.domain.entities import (
    FunctionExecutionClaim,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionType,
)


class _UowTracker:
    active = 0

    @asynccontextmanager
    async def factory(self):
        self.active += 1
        try:
            yield object()
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_dispatcher_uses_exact_process_operation_and_no_db_during_io(
    monkeypatch,
) -> None:
    tracker = _UowTracker()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    claim = FunctionExecutionClaim(
        run_id=uuid4(),
        attempt_id=uuid4(),
        operation_id=uuid4(),
        fence=1,
        pod_id=uuid4(),
        function_id=uuid4(),
        revision_id=uuid4(),
        function_type=FunctionType.API,
        deadline_at=deadline,
        ticket="fat_" + "x" * 43,
        runtime_token="far_" + "y" * 43,
    )
    pending = FunctionRunEntity(
        id=claim.run_id,
        function_id=claim.function_id,
        revision_id=claim.revision_id,
        user_id=uuid4(),
        status=FunctionRunStatus.PENDING,
        deadline_at=deadline,
    )
    completed = pending.model_copy(
        update={"status": FunctionRunStatus.COMPLETED, "output_data": {"ok": True}}
    )
    callback_completed = False

    class _ExecutionRepository:
        def __init__(self, _uow, _signer):
            pass

        async def claim_run(self, *_args, **_kwargs):
            return claim

        async def mark_process_started(self, *_args, **_kwargs):
            return None

        async def fail_dispatch(self, *_args, **_kwargs):
            raise AssertionError("successful execution must not fail")

    class _RunRepository:
        def __init__(self, _uow):
            pass

        async def get_run(self, _run_id):
            return completed if callback_completed else pending

    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher."
        "FunctionExecutionRepository",
        _ExecutionRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.FunctionRunRepository",
        _RunRepository,
    )
    monkeypatch.setattr(
        "app.modules.function.application.function_dispatcher.settings."
        "function_runtime_gateway_url",
        "https://gateway.lemma.test",
    )

    start_operations = []

    class _Client:
        async def ensure_sandbox(self, kind, logical_id, **_kwargs):
            assert tracker.active == 0
            return SandboxHandle(
                workload_kind=kind,
                logical_id=logical_id,
                desired_state="present",
                profile=ProfileRef(name="function-python-v1", digest=f"sha256:{'2' * 64}"),
                allocation_state="active",
                allocation_id=uuid4(),
                allocation_epoch=1,
                ready=True,
                operation_id=None,
                retry_after_ms=None,
            )

        async def start_process(self, kind, logical_id, **kwargs):
            assert tracker.active == 0
            start_operations.append(kwargs["operation_id"])
            if len(start_operations) == 1:
                request = httpx.Request("POST", "https://agentbox.test/processes")
                raise httpx.ReadError("lost response", request=request)
            assert kind == WorkloadKind.FUNCTION
            assert logical_id == claim.pod_id
            return ProcessRef(
                operation_id=claim.operation_id,
                allocation_id=uuid4(),
                allocation_epoch=1,
                state=ProcessState.RUNNING,
                cwd="/tmp",
                tty=False,
                output_limit_bytes=8 * 1024 * 1024,
                deadline_at=deadline,
                started_at=datetime.now(timezone.utc),
                completed_at=None,
                exit_code=None,
            )

        async def send_process_input(self, kind, logical_id, operation_id, data, **_kwargs):
            nonlocal callback_completed
            assert tracker.active == 0
            assert (kind, logical_id, operation_id) == (
                WorkloadKind.FUNCTION,
                claim.pod_id,
                claim.operation_id,
            )
            assert data == f"{claim.ticket}\n".encode()
            callback_completed = True

        async def read_process_output(self, *_args, **_kwargs):
            return ProcessOutputSnapshot(
                chunks=(),
                next_sequence=0,
                truncated_before_sequence=None,
                state=ProcessState.RUNNING,
                exit_code=None,
            )

        async def close(self):
            assert tracker.active == 0

    dispatcher = FunctionDispatcher(
        uow_factory=tracker.factory,
        credential_signer=FunctionAttemptCredentialSigner("d" * 32),
        agentbox_client_factory=_Client,
        worker_id="test-worker",
    )
    result = await dispatcher.execute(claim.run_id)

    assert result.status == FunctionRunStatus.COMPLETED
    assert start_operations == [claim.operation_id, claim.operation_id]
    assert tracker.active == 0
