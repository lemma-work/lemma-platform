"""Starting a named service on a machine, for the agent module's
``exec_command(service=...)``.

The agent's command already went through the machine resolver, the run slot
and the session that minted its token; this records the service durably, with
the claims that token was minted for, so the machine brings it back -- with a
fresh token -- after it sleeps.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.modules.workspace.domain.machine_errors import MachineConflict

if TYPE_CHECKING:
    from app.modules.workspace.services.machine_processes import StartProcess


@dataclass(frozen=True, slots=True)
class AgentService:
    machine_id: UUID
    name: str
    command: str
    #: The session's directory; ``workdir`` is read from it.
    session_cwd: str
    workdir: str | None
    #: The session's environment: its Lemma token among it.
    base_env: dict[str, str]
    user_id: UUID
    pod_id: UUID | None
    organization_id: UUID | None
    workload_type: str | None
    workload_id: UUID | None
    workload_name: str | None
    conversation_id: UUID | None
    #: The run's delegation scope; empty when it is unscoped.
    scope: tuple[str, ...] = ()
    #: The session the run's token was minted under. A restart mints under
    #: the same one, so the approvals the person gave there still answer.
    session_id: str | None = None


def _cwd(service: AgentService) -> str:
    from sandbox_runtime.paths import is_inside_home

    target = posixpath.normpath(
        posixpath.join(service.session_cwd, service.workdir or ".")
    )
    if not is_inside_home(target):
        raise MachineConflict(
            "A service's directory must be inside the machine's home.",
            code="MACHINE_PATH_OUTSIDE_HOME",
        )
    return target


def _start_request(service: AgentService) -> StartProcess:
    from app.modules.workspace.services.machine_processes import StartProcess
    from app.modules.workspace.services.machine_services import TokenClaims

    return StartProcess(
        command=service.command,
        cwd=_cwd(service),
        service=service.name,
        restart_on_resume=True,
        started_by=service.user_id,
        claims=TokenClaims(
            user_id=service.user_id,
            pod_id=service.pod_id,
            organization_id=service.organization_id,
            workload_type=service.workload_type,
            workload_id=service.workload_id,
            workload_name=service.workload_name,
            conversation_id=service.conversation_id,
            # Empty is unscoped, in the token as in the authorizer
            # (`workload_authority`): None says the same, and is what
            # the run's own shell token was minted with.
            scope=service.scope or None,
            session_id=service.session_id,
        ),
    )


async def start_agent_service(service: AgentService) -> str:
    """Start it, and return its process id."""
    from app.modules.workspace.services.machine_processes import MachineProcesses

    record = await MachineProcesses().start_process(
        service.machine_id, _start_request(service), base_env=service.base_env
    )
    return record.process_id


__all__ = ["AgentService", "start_agent_service"]
