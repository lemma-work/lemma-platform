from __future__ import annotations

from typing import Optional
from uuid import UUID

from agentbox_client import AgentBoxClient
from agentbox_client.generated.manager.models import SandboxSummary

from app.core.config import settings
from app.modules.workspace.contracts import SandboxInfo
from app.modules.workspace.services.interfaces import ISandbox


def agentbox_sandbox_id(user_id: UUID) -> str:
    return user_id.hex


class AgentBoxSandbox(ISandbox):
    """User sandbox backed by the external AgentBox manager API."""

    def __init__(self) -> None:
        api_key = settings.agentbox_api_key
        if not api_key:
            raise RuntimeError(
                "AGENTBOX_API_KEY is required for workspace sandboxes"
            )
        self.client = AgentBoxClient(
            base_url=settings.agentbox_api_url,
            api_key=api_key,
            timeout_seconds=300.0,
        )

    async def ensure_sandbox(
        self,
        user_id: UUID,
        *,
        env: dict[str, str] | None = None,
    ) -> SandboxInfo:
        sandbox_id = agentbox_sandbox_id(user_id)
        sandbox = await self.client.ensure_sandbox(
            sandbox_id,
            env=env,
        )
        return self._to_container_info(sandbox_id, sandbox)

    async def get_sandbox(self, user_id: UUID) -> Optional[SandboxInfo]:
        sandbox_id = agentbox_sandbox_id(user_id)
        sandbox = await self.client.get_sandbox(sandbox_id)
        if sandbox is None:
            return None
        return self._to_container_info(sandbox_id, sandbox)

    async def delete_sandbox(self, user_id: UUID) -> None:
        await self.client.delete_sandbox(agentbox_sandbox_id(user_id))

    async def suspend_sandbox(self, user_id: UUID) -> None:
        await self.client.suspend_sandbox(agentbox_sandbox_id(user_id))

    async def is_sandbox_running(self, user_id: UUID) -> bool:
        info = await self.get_sandbox(user_id)
        return info is not None and info.status == "RUNNING"

    async def heartbeat(self, user_id: UUID) -> None:
        await self.client.heartbeat_sandbox(agentbox_sandbox_id(user_id))

    async def close(self) -> None:
        await self.client.close()

    def _to_sandbox_info(self, sandbox_id: str, sandbox: SandboxSummary) -> SandboxInfo:
        return SandboxInfo(
            sandbox_id=sandbox_id,
            name=sandbox.id,
            namespace=None,
            status=sandbox.status,
            image="",
            created_at=None,
            endpoint=f"agentbox://{sandbox_id}",
        )

    def _to_container_info(
        self,
        sandbox_id: str,
        sandbox: SandboxSummary,
    ) -> SandboxInfo:
        return self._to_sandbox_info(sandbox_id, sandbox)
