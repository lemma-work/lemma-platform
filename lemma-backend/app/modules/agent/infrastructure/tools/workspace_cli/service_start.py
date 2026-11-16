"""``exec_command(service=...)``: a command started as a named service.

Kept beside the tool rather than in it: the service path shares the
session the tool opens and nothing else.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.authority import workspace_principal
from app.modules.agent.infrastructure.tools.workspace_cli.models import (
    ExecCommandRequest,
    ExecCommandResult,
)


async def start_service(
    ctx: ToolContext, request: ExecCommandRequest, session, runtime
) -> ExecCommandResult:
    """``exec_command(service=...)``: the command recorded as a named service
    on the machine the session is on, with the session's token and the claims
    it was minted for, so the machine can start it again after it sleeps."""
    from app.modules.workspace.contracts.machine_services import (
        AgentService,
        start_agent_service,
    )

    on_host = (
        getattr(ctx, "host_workspace", None) is not None
        and not request.machine
        and not getattr(ctx, "machine", None)
    )
    if on_host:
        return ExecCommandResult(
            success=False,
            completed=True,
            error="Services run on a machine, not on your own computer's shell.",
        )
    principal = workspace_principal(ctx)
    process_id = await start_agent_service(
        AgentService(
            machine_id=UUID(str(session.sandbox_id)),
            name=str(request.service),
            command=request.cmd,
            session_cwd=session.cwd,
            workdir=request.workdir,
            base_env=dict(session.env_vars),
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            organization_id=ctx.organization_id,
            workload_type=principal.workload_type,
            workload_id=principal.workload_id,
            workload_name=principal.workload_name,
            conversation_id=ctx.conversation_id,
        )
    )
    if session.session_id:
        await runtime.bind_process_to_session(
            process_id=process_id, session_id=session.session_id
        )
    return ExecCommandResult(
        success=True,
        stdout=f"Started service {request.service!r} (process {process_id}).",
        completed=False,
        process_id=process_id,
    )


__all__ = ["start_service"]
