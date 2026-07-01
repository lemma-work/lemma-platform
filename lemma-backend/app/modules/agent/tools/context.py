"""Agent tool context models.

The domain ``AgentContext`` stays small and framework-neutral. Tool execution
uses this subtype so copied tools can access workspace/file-manager helpers
without importing anything from the old agent module.
"""

from __future__ import annotations

import os
from uuid import UUID

import aiofiles
from pydantic import BaseModel, Field

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.subscription_models import SubscriptionModels
from app.modules.agent.services.subscription_models_provider import (
    resolve_subscription_models,
)
from app.modules.workspace.services.workspace_file_manager import WorkspaceFileManager


class BaseAgentContext(AgentContext):
    """Context passed to Pydantic AI toolsets."""

    workload_type: str | None = "agent"
    workload_id: UUID | None = None
    configured_accounts: dict[str, UUID] = Field(default_factory=dict)
    surface_id: UUID | None = None
    surface_platform: str | None = None
    surface_metadata: object | None = None
    external_channel_id: str | None = None
    external_thread_id: str | None = None
    external_user_id: str | None = None
    external_message_id: str | None = None
    agent_display_name: str | None = None
    runtime_profile: dict[str, object] | None = None
    runtime_credentials: dict[str, object] = Field(default_factory=dict)
    workspace_id: str = "default"
    workspace_cwd: str | None = None
    # Default pod-filesystem working directory for this conversation, e.g.
    # `/me/c/{date}/{slug}`. Relative pod tool paths resolve against this.
    pod_cwd: str | None = None

    # True only when this tool runs inside the in-process pydantic harness, which
    # catches the AgentInputRequired pause signal and turns it into a clean run
    # termination + WAITING conversation. Daemon/MCP and other dispatch paths leave
    # this False: they own their own session and cannot be paused mid tool-call, so
    # ask_user/request_approval fall back to conversational guidance instead.
    supports_pause_signal: bool = False

    @property
    def organization_id(self) -> UUID | None:
        """Compatibility alias used by workspace tooling."""
        return self.org_id

    @property
    def file_manager(self) -> WorkspaceFileManager:
        return WorkspaceFileManager(
            self.user_id,
            cwd=self.get_workspace_cwd().removeprefix("/workspace/"),
        )

    async def get_subscription_models(self) -> SubscriptionModels:
        return await resolve_subscription_models(self.user_id)

    def get_workspace_cwd(self) -> str:
        return self.workspace_cwd or f"/workspace/conversations/{self.conversation_id}"

    def get_pod_cwd(self) -> str:
        # Callers on the main run path always set `pod_cwd` explicitly (see
        # `resolve_pod_cwd`); this conversation_id-based fallback only covers
        # secondary context-construction sites that haven't set it.
        return self.pod_cwd or f"/me/conversations/{self.conversation_id}"

    def get_workspace_scope_key(self) -> str:
        return f"workspace:{self.workspace_id}:conversation:{self.conversation_id}"


class ConversationContext(BaseAgentContext):
    """Compatibility name for copied conversation-oriented tools."""


class BaseToolResponse(BaseModel):
    """Base class for tool responses."""

    success: bool = Field(
        default=False,
        description="Whether the tool completed successfully.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error details when the tool fails.",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable status or follow-up information for the tool call.",
    )


async def get_prompt(prompt_name: str) -> str:
    """Load an Agent prompt by name."""
    current_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(current_dir, f"prompts/{prompt_name}.md")
    async with aiofiles.open(path, "r") as file:
        return await file.read()
