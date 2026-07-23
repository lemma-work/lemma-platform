from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from agentbox_client import AgentBoxApiError, AgentBoxClient
from agentbox_client import (
    AdmissionClass,
    ProfileRef,
    RetryDisposition,
    SandboxHandle,
    WorkloadKind,
)

from app.core.config import settings
from app.core.request_context import correlation_headers
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.interfaces import ISandbox


def agentbox_sandbox_id(user_id: UUID) -> UUID:
    return user_id


class AgentBoxSandbox(ISandbox):
    """User sandbox backed by the external AgentBox manager API."""

    def __init__(self) -> None:
        api_key = settings.agentbox_api_key
        if not api_key:
            raise RuntimeError("AGENTBOX_API_KEY is required for workspace sandboxes")
        self.client = AgentBoxClient(
            base_url=settings.agentbox_api_url,
            api_key=api_key,
            timeout_seconds=300.0,
            context_headers_provider=correlation_headers,
        )

    async def ensure_sandbox(
        self,
        user_id: UUID,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        sandbox_id = agentbox_sandbox_id(user_id)
        del env
        deadline_at = self._deadline()
        profile = ProfileRef(
            name=settings.agentbox_workspace_profile_name,
            digest=settings.agentbox_workspace_profile_digest,
        )
        while datetime.now(timezone.utc) < deadline_at:
            try:
                sandbox = await self.client.ensure_sandbox(
                    WorkloadKind.WORKSPACE,
                    sandbox_id,
                    profile=profile,
                    admission_class=AdmissionClass.INTERACTIVE,
                    deadline_at=deadline_at,
                )
            except AgentBoxApiError as exc:
                if exc.retry != RetryDisposition.WAIT:
                    raise
                await self._wait(exc.retry_after_ms, deadline_at)
                continue
            if sandbox.ready:
                return self._to_container_info(str(sandbox_id), sandbox)
            await self._wait(sandbox.retry_after_ms, deadline_at)
        raise TimeoutError(f"workspace sandbox {sandbox_id} did not become ready")

    async def get_sandbox(self, user_id: UUID) -> Optional[SandboxInfo]:
        sandbox_id = agentbox_sandbox_id(user_id)
        sandbox = await self.client.inspect_sandbox(WorkloadKind.WORKSPACE, sandbox_id)
        if sandbox is None:
            return None
        return self._to_container_info(str(sandbox_id), sandbox)

    async def delete_sandbox(self, user_id: UUID) -> None:
        await self.client.destroy_sandbox(
            WorkloadKind.WORKSPACE,
            agentbox_sandbox_id(user_id),
            deadline_at=self._deadline(),
        )

    async def suspend_sandbox(self, user_id: UUID) -> None:
        await self.client.release_sandbox(
            WorkloadKind.WORKSPACE,
            agentbox_sandbox_id(user_id),
            deadline_at=self._deadline(),
        )

    async def is_sandbox_running(self, user_id: UUID) -> bool:
        info = await self.get_sandbox(user_id)
        return info is not None and info.status == "RUNNING"

    async def close(self) -> None:
        await self.client.close()

    def _to_sandbox_info(self, sandbox_id: str, sandbox: SandboxHandle) -> SandboxInfo:
        return SandboxInfo(
            sandbox_id=sandbox_id,
            name=sandbox_id,
            namespace=None,
            status="RUNNING" if sandbox.ready else "STOPPED",
            image="",
            created_at=None,
            endpoint=f"agentbox://{sandbox_id}",
        )

    def _to_container_info(
        self,
        sandbox_id: str,
        sandbox: SandboxHandle,
    ) -> SandboxInfo:
        return self._to_sandbox_info(sandbox_id, sandbox)

    @staticmethod
    def _deadline() -> datetime:
        return datetime.now(timezone.utc) + timedelta(minutes=5)

    @staticmethod
    async def _wait(retry_after_ms: int | None, deadline_at: datetime) -> None:
        remaining = (deadline_at - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        delay = max(0.05, (retry_after_ms or 250) / 1000)
        await asyncio.sleep(min(delay, remaining))
