"""Best-effort cancellation lifecycle for remote function executor clients."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.core.log.log import get_logger

logger = get_logger(__name__)


class _ClosableExecutorClient(Protocol):
    async def close(self) -> None: ...


async def cancel_executor_run(client: object, sandbox_id: str, run_id: UUID) -> None:
    """Cancel when supported without masking the caller's timeout/cancellation."""

    cancel = getattr(client, "cancel", None)
    if not callable(cancel):
        return
    try:
        await cancel(sandbox_id=sandbox_id, run_id=run_id)
    except (httpx.HTTPError, json.JSONDecodeError, ValidationError):
        logger.debug(
            'function.function_executor_cancellation.function_executor_cancellation_sandbox_s.diagnostic',
            sandbox_id=sandbox_id,
            run_id=run_id,
        )


@asynccontextmanager
async def managed_executor_client(
    client: _ClosableExecutorClient, sandbox_id: str, run_id: UUID
) -> AsyncIterator[None]:
    """Close a client and remotely cancel its run if the caller is cancelled."""

    try:
        yield
    except asyncio.CancelledError:
        await asyncio.shield(cancel_executor_run(client, sandbox_id, run_id))
        raise
    finally:
        await client.close()
