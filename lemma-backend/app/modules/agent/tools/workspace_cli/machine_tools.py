"""The machine tools beside `exec_command`: which machines there are, a preview
link for a port, copying files to and from the pod, and cleaning up.

Each one decides the machine the same way a command does -- the call's
`machine`, else the agent's, else the person's own -- through the machine
resolver, so a tool cannot reach a machine a command could not.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.core.log.log import get_logger
from app.modules.agent.tools.context import TOOL_COMMENT_DESC, BaseAgentContext
from app.modules.agent.tools.tool_errors import safe_described_error
from app.modules.agent.tools.workspace_cli.run_machine import resolve_run_machine
from app.modules.agent.tools.workspace_cli.workspace_cli import refuse_owner_workspace

logger = get_logger(__name__)

_MACHINE_DESC = (
    "Which machine: `me`, `pod:<name>`, or one from `list_machines`. "
    "Omit for your usual one."
)


class ListMachinesRequest(BaseModel):
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class MachineSummary(BaseModel):
    ref: str
    name: str
    kind: str
    size: str
    template: str
    status: str
    docker: bool
    yours: bool = Field(
        description="Whether commands go here when you name no machine."
    )


class ListMachinesResult(BaseModel):
    success: bool
    machines: list[MachineSummary] = Field(default_factory=list)
    error: Optional[str] = None


class ExposePortRequest(BaseModel):
    port: int = Field(ge=1, le=65535, description="The port a server is listening on.")
    visibility: Literal["private", "link"] = Field(
        default="private",
        description=(
            "`private`: people who can see this machine, after signing in. "
            "`link`: anyone with the URL, for at most 24 hours."
        ),
    )
    ttl_seconds: Optional[int] = Field(
        default=None,
        ge=60,
        description="How long the link lives (link previews: at most 24h).",
    )
    machine: Optional[str] = Field(default=None, description=_MACHINE_DESC)
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class ExposePortResult(BaseModel):
    success: bool
    url: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class SyncFilesRequest(BaseModel):
    direction: Literal["to_pod", "from_pod"] = Field(
        description="`to_pod` copies machine files into a pod folder; `from_pod` the reverse."
    )
    machine_path: str = Field(
        description="A file or folder on the machine (absolute, or relative to your working directory)."
    )
    pod_path: str = Field(
        description="`to_pod`: the pod folder to copy into (e.g. `/me/renders`). `from_pod`: the pod file to copy."
    )
    machine: Optional[str] = Field(default=None, description=_MACHINE_DESC)
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class SyncFilesResult(BaseModel):
    success: bool
    files: list[str] = Field(default_factory=list)
    bytes: int = 0
    error: Optional[str] = None


class ResetMachineRequest(BaseModel):
    scopes: list[Literal["processes", "runs", "caches", "docker"]] = Field(
        min_length=1,
        description=(
            "What to clean: `processes` (stops everything but services), `runs` "
            "(finished runs' folders), `caches` (package caches), `docker` "
            "(unused images, build cache, stopped containers). Logins are never touched."
        ),
    )
    machine: Optional[str] = Field(default=None, description=_MACHINE_DESC)
    comment: Optional[str] = Field(default=None, description=TOOL_COMMENT_DESC)


class ResetMachineResult(BaseModel):
    success: bool
    freed_bytes: int = 0
    details: list[str] = Field(default_factory=list)
    error: Optional[str] = None


def _failure(exc: BaseException) -> str:
    return safe_described_error(exc)


def _tool_failures() -> tuple[type[BaseException], ...]:
    """What a machine tool reports to the agent instead of raising: a refusal
    with a sentence (`DomainError`), the machine not answering, or the network
    under it. Anything else is a defect and surfaces as one."""
    import httpx

    from app.core.domain.errors import DomainError
    from app.modules.workspace.contracts.runtime_errors import machine_runtime_errors

    return (
        DomainError,
        OSError,
        ValueError,
        httpx.HTTPError,
        *machine_runtime_errors(),
    )


async def list_machines_internal(
    ctx: BaseAgentContext, request: ListMachinesRequest
) -> ListMachinesResult:
    del request
    from app.modules.workspace.contracts.agent_machines import machines_for_agent

    try:
        refuse_owner_workspace(ctx)
        found = await machines_for_agent(
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            agent_id=ctx.workload_id,
            conversation_id=ctx.conversation_id,
            usual=getattr(ctx, "machine", None),
        )
    except _tool_failures() as exc:
        logger.warning("agent.machine_tools.list_failed.degraded", exc_info=True)
        return ListMachinesResult(success=False, error=_failure(exc))
    return ListMachinesResult(
        success=True,
        machines=[MachineSummary(**summary) for summary in found],
    )


async def expose_port_internal(
    ctx: BaseAgentContext, request: ExposePortRequest
) -> ExposePortResult:
    from app.modules.workspace.contracts.agent_machines import expose_port

    try:
        refuse_owner_workspace(ctx)
        target = await resolve_run_machine(ctx, request.machine)
        url, expires_at = await expose_port(
            target,
            port=request.port,
            visibility=request.visibility,
            ttl_seconds=request.ttl_seconds,
            created_by=ctx.user_id,
        )
    except _tool_failures() as exc:
        logger.warning("agent.machine_tools.expose_failed.degraded", exc_info=True)
        return ExposePortResult(success=False, error=_failure(exc))
    return ExposePortResult(success=True, url=url, expires_at=expires_at)


async def sync_files_internal(
    ctx: BaseAgentContext, request: SyncFilesRequest
) -> SyncFilesResult:
    from app.modules.workspace.contracts.agent_machines import sync_files

    try:
        refuse_owner_workspace(ctx)
        target = await resolve_run_machine(ctx, request.machine)
        files, size = await sync_files(
            target,
            direction=request.direction,
            machine_path=request.machine_path,
            pod_id=ctx.pod_id,
            pod_path=request.pod_path,
            user_id=ctx.user_id,
            cwd=target.run_workdir or ctx.get_workspace_cwd(),
        )
    except _tool_failures() as exc:
        logger.warning("agent.machine_tools.sync_failed.degraded", exc_info=True)
        return SyncFilesResult(success=False, error=_failure(exc))
    return SyncFilesResult(success=True, files=files, bytes=size)


async def reset_machine_internal(
    ctx: BaseAgentContext, request: ResetMachineRequest
) -> ResetMachineResult:
    from app.modules.workspace.contracts.agent_machines import reset_for_agent

    try:
        refuse_owner_workspace(ctx)
        target = await resolve_run_machine(ctx, request.machine)
        freed, details = await reset_for_agent(
            target, scopes=list(request.scopes), acting_user=ctx.user_id
        )
    except _tool_failures() as exc:
        logger.warning("agent.machine_tools.reset_failed.degraded", exc_info=True)
        return ResetMachineResult(success=False, error=_failure(exc))
    return ResetMachineResult(success=True, freed_bytes=freed, details=details)


__all__ = [
    "ExposePortRequest",
    "ListMachinesRequest",
    "ResetMachineRequest",
    "SyncFilesRequest",
]
