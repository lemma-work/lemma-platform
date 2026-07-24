from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agentbox.domain import AgentBoxError, MaintenanceAction
from agentbox.lifecycle import SandboxLifecycleService
from agentbox.persistence.uow import StateDatabase
from agentbox.telemetry import observed_control_operation


class SandboxMaintenanceWorker:
    """Run distributed idle cleanup using short durable claims."""

    def __init__(
        self,
        database: StateDatabase,
        lifecycle: SandboxLifecycleService,
        *,
        workspace_idle_seconds: float,
        function_idle_seconds: float,
        batch_size: int = 32,
    ) -> None:
        self._database = database
        self._lifecycle = lifecycle
        self._workspace_idle = timedelta(seconds=workspace_idle_seconds)
        self._function_idle = timedelta(seconds=function_idle_seconds)
        self._batch_size = batch_size

    @observed_control_operation("cleanup")
    async def run_once(self, *, deadline_at: datetime) -> int:
        completed = 0
        for _ in range(self._batch_size):
            now = datetime.now(timezone.utc)
            if now >= deadline_at:
                break
            async with self._database.uow() as uow:
                claims = await uow.repository.claim_due_maintenance(
                    workspace_idle_before=now - self._workspace_idle,
                    function_idle_before=now - self._function_idle,
                    claimed_until=deadline_at,
                    now=now,
                    limit=1,
                )
                await uow.commit()
            if not claims:
                break
            claim = claims[0]
            try:
                if claim.action == MaintenanceAction.RELEASE:
                    await self._lifecycle.release(
                        claim.key,
                        deadline_at=deadline_at,
                        _claim=claim,
                    )
                else:
                    await self._lifecycle.destroy(
                        claim.key,
                        deadline_at=deadline_at,
                        _claim=claim,
                    )
            except AgentBoxError:
                # The durable claim expires and is retried with the same
                # provider allocation identity on a later pass.
                continue
            completed += 1
        return completed


async def maintenance_loop(
    worker: SandboxMaintenanceWorker,
    *,
    interval_seconds: float,
    operation_timeout_seconds: float,
) -> None:
    while True:
        deadline = datetime.now(timezone.utc) + timedelta(
            seconds=operation_timeout_seconds
        )
        try:
            await worker.run_once(deadline_at=deadline)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Claims are durable and expire, so later bounded passes continue.
            pass
        await asyncio.sleep(interval_seconds)
