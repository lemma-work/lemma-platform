"""Agent tool context models.

The domain ``AgentContext`` stays small and framework-neutral. Tool execution
uses this subtype so copied tools can access workspace/file-manager helpers
without importing anything from the old agent module.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from sandbox_runtime.paths import WORKSPACE_ROOT
from app.modules.agent.domain.context import AgentContext
from app.modules.agent.domain.subscription_models import SubscriptionModels
from app.modules.agent.domain.vision import AgentVisionMode
from app.modules.agent.services.subscription_models_provider import (
    resolve_subscription_models,
)
from app.modules.agent.services.workspace_location import (
    ProjectRepo,
    pod_cwd_from_workspace_cwd,
)
from app.modules.workspace.contracts.host_execution import HostWorkspace
from app.modules.workspace.contracts.tooling import WorkspaceFileManager


TOOL_COMMENT_DESC = "One-line statement of intent, shown to the user."


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
    # The GitHub repository this conversation works in, when it was started
    # against a project rather than a scratchpad. Drives the clone-on-first-use
    # step in the workspace CLI; None means the ordinary scratchpad cwd.
    workspace_repo: ProjectRepo | None = None
    # Default pod-filesystem working directory for this conversation, e.g.
    # `/me/c/{date}/{slug}`. Relative pod tool paths resolve against this.
    pod_cwd: str | None = None
    # Set when this run's commands and files execute on the installation
    # owner's Mac rather than in the VM (docs/architecture/
    # desktop-host-execution.md). Chosen once, when the run's context is built,
    # and carried for every tool call of the run: a run never moves between
    # the two. The browser stays in the VM whatever this says.
    host_workspace: HostWorkspace | None = None
    # An Agent Host (coding agent) run whose owner has host execution on: the
    # agent already runs on the Mac with its own shell and file tools, so
    # Lemma's duplicates are withheld (§7).
    host_runs_native_commands: bool = False

    # How image-returning tools should answer on this run. Transient (derived
    # from the resolved model each run), never persisted. UNAVAILABLE is the
    # safe default: a tool that does not know the mode must not emit image
    # content, because a text-only model rejects the whole request when it
    # arrives. See `domain/vision`.
    vision_mode: AgentVisionMode = AgentVisionMode.UNAVAILABLE

    # True only when this tool runs inside the in-process pydantic harness, which
    # catches the AgentInputRequired pause signal and turns it into a clean run
    # termination + WAITING conversation. Remote/MCP and other dispatch paths leave
    # this False: they own their own session and cannot be paused mid tool-call, so
    # ask_user/request_approval fall back to conversational guidance instead.
    supports_pause_signal: bool = False

    @property
    def organization_id(self) -> UUID | None:
        """Compatibility alias used by workspace tooling."""
        return self.org_id

    @property
    def file_manager(self) -> WorkspaceFileManager:
        # Passed as stored, absolute. The manager splits it and keeps the root
        # it was written under -- a `removeprefix` of the *current* root did
        # nothing for a cwd under the previous one, leaving an absolute string
        # where a relative one was required.
        # The VM workspace's cwd even on a host-execution run: this manager
        # reaches the VM, and the host root means nothing there.
        return WorkspaceFileManager(
            self.user_id, cwd=self.workspace_cwd or WORKSPACE_ROOT
        )

    async def get_subscription_models(self) -> SubscriptionModels:
        return await resolve_subscription_models(self.user_id)

    @property
    def host_execution_mode(self) -> Literal["native", "sandbox"] | None:
        """How this run's commands reach the owner's Mac, if they do.

        ``"native"``: an Agent Host run whose own tools are on the Mac.
        ``"sandbox"``: an in-process run whose ``exec_command`` runs there.
        """
        if self.host_runs_native_commands:
            return "native"
        if self.host_workspace is not None:
            return "sandbox"
        return None

    def get_workspace_cwd(self) -> str:
        """This conversation's directory, or the project root when there is none.

        The root, not a directory named after the conversation id. A cwd of that
        shape is not what `resolve_workspace_location` produces -- conversations
        live at `c/{date}/{slug}` -- so inventing one here put the agent
        somewhere its own metadata did not name, which is the disagreement
        between the prompt and the tools that this path has already caused once.

        Every caller that *has* a conversation resolves the real cwd and passes
        it. The one that does not is the pod MCP bridge, which serves a client
        holding a pod token and carries a nil conversation id -- so the fallback
        it used to take named a directory after all-zeroes. The project root is
        the honest answer for "no conversation".
        """
        if self.host_workspace is not None:
            return self.host_workspace.root
        return self.workspace_cwd or WORKSPACE_ROOT

    def get_pod_cwd(self) -> str:
        # Callers on the main run path always set `pod_cwd` explicitly (see
        # `resolve_pod_cwd`); this fallback only covers secondary
        # context-construction sites that haven't set it. It mirrors the
        # workspace cwd rather than naming the conversation id, because the two
        # directories are meant to be the same short path under two roots -- a
        # fallback of its own shape scattered pod writes under
        # `/me/conversations/<uuid>`, where nothing else ever looks.
        return self.pod_cwd or pod_cwd_from_workspace_cwd(self.get_workspace_cwd())

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
