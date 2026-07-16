"""Short-request polling for accepted AgentBox function runs."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from uuid import UUID

from agentbox_client.apps.function_executor import (
    FunctionExecutorClient,
    FunctionInvokeResponse,
)

from app.composition.function_workspace import retry_on_transient_agentbox_error
from app.modules.function.domain.errors import FunctionValidationError
from app.modules.function.services.function_executor_cancellation import (
    cancel_executor_run,
    managed_executor_client,
)


async def poll_executor_job(
    *,
    client: FunctionExecutorClient,
    sandbox_id: str,
    run_id: UUID,
    timeout_seconds: int,
    retry_max_attempts: int,
    poll_interval_seconds: int,
) -> FunctionInvokeResponse:
    """Poll one idempotent run without holding an E2B request open."""

    deadline = time.monotonic() + timeout_seconds
    while True:
        status = await retry_on_transient_agentbox_error(
            lambda: client.get_status(sandbox_id=sandbox_id, run_id=run_id),
            max_attempts=retry_max_attempts,
        )
        if status.status in {"completed", "failed", "cancelled", "timeout"}:
            logs = await retry_on_transient_agentbox_error(
                lambda: client.get_logs(sandbox_id=sandbox_id, run_id=run_id),
                max_attempts=retry_max_attempts,
            )
            return FunctionInvokeResponse(
                status=status.status,
                output_data=status.output_data,
                error=status.error,
                logs=logs.logs,
                code_hash=status.code_hash or "",
                duration_ms=status.duration_ms or 0,
            )
        if time.monotonic() >= deadline:
            await cancel_executor_run(client, sandbox_id, run_id)
            raise TimeoutError("Function job did not finish before timeout")
        await asyncio.sleep(poll_interval_seconds)


async def poll_session_executor_job(
    *,
    session: object,
    run_id: UUID,
    timeout_seconds: int,
    retry_max_attempts: int,
    poll_interval_seconds: int,
    client_factory: Callable[[str], FunctionExecutorClient],
) -> FunctionInvokeResponse:
    """Build the authenticated client and poll one session's accepted run."""

    env_vars = getattr(session, "env_vars", {}) or {}
    lemma_token = env_vars.get("LEMMA_TOKEN")
    sandbox_id = getattr(session, "sandbox_id", None)
    if not lemma_token:
        raise FunctionValidationError("Workspace session did not include LEMMA_TOKEN")
    if not sandbox_id:
        raise FunctionValidationError("Workspace session did not include sandbox_id")
    client = client_factory(lemma_token)
    async with managed_executor_client(client, sandbox_id, run_id):
        return await poll_executor_job(
            client=client,
            sandbox_id=sandbox_id,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
            retry_max_attempts=retry_max_attempts,
            poll_interval_seconds=poll_interval_seconds,
        )
