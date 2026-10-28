"""What the rest of the agent module may use of the Agent Host.

Nothing outside ``agent_host/`` imports any other file in it
(``tests/unit/test_agent_host_boundary``). Each name resolves on first use, so
importing this file costs nothing: a caller that only needs a status enum does
not load the link, and the worker's harness stays out of the API process.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.agent.agent_host.control.channels import (
        poke_host as poke_host,
    )
    from app.modules.agent.agent_host.control.dispatch_repository import (
        AgentHostDispatchRepository as AgentHostDispatchRepository,
    )
    from app.modules.agent.agent_host.control.final_answer import (
        store_final_answer as store_final_answer,
    )
    from app.modules.agent.agent_host.control.host_execution import (
        host_execution_host_id as host_execution_host_id,
        host_for_host_sandbox as host_for_host_sandbox,
        is_paired_to_any_of as is_paired_to_any_of,
    )
    from app.modules.agent.agent_host.control.host_execution_selection import (
        choose_host_workspace as choose_host_workspace,
        host_folder_for as host_folder_for,
        recorded_host_workspace as recorded_host_workspace,
    )
    from app.modules.agent.agent_host.control.native_commands import (
        settle_native_commands as settle_native_commands,
    )
    from app.modules.agent.agent_host.control.ops import (
        AgentHostOpClient as AgentHostOpClient,
    )
    from app.modules.agent.agent_host.control.recovery import (
        cancel_abandoned_host_runs as cancel_abandoned_host_runs,
        cleanup_retained_state as cleanup_retained_state,
        reconcile_expired_leases as reconcile_expired_leases,
    )
    from app.modules.agent.agent_host.control.repository import (
        AgentHostRepository as AgentHostRepository,
    )
    from app.modules.agent.agent_host.controllers import (
        routers as routers,
    )
    from app.modules.agent.agent_host.controllers.schemas import (
        AgentHostHarnessResponse as AgentHostHarnessResponse,
    )
    from app.modules.agent.agent_host.domain.host import (
        AgentHostHarnessHealth as AgentHostHarnessHealth,
        AgentHostStatus as AgentHostStatus,
        effective_agent_host_status as effective_agent_host_status,
    )
    from app.modules.agent.agent_host.domain.ops import (
        HOST_OFFLINE as HOST_OFFLINE,
        HOST_OFFLINE_MESSAGE as HOST_OFFLINE_MESSAGE,
        AgentHostOpError as AgentHostOpError,
    )
    from app.modules.agent.agent_host.domain.selections import (
        validate_agent_host_model as validate_agent_host_model,
        validate_agent_host_selections as validate_agent_host_selections,
    )
    from app.modules.agent.agent_host.harness.artifacts import (
        PodFileAgentHostArtifactWriter as PodFileAgentHostArtifactWriter,
    )
    from app.modules.agent.agent_host.harness.harness import (
        RemoteHarness as RemoteHarness,
    )
    from app.modules.agent.agent_host.harness.pre_approvals import (
        host_pre_approves as host_pre_approves,
    )
    from app.modules.agent.agent_host.link.registry import (
        link_registry as link_registry,
    )

_PACKAGE = "app.modules.agent.agent_host"

#: Each exported name, by the file under ``agent_host/`` that defines it.
_EXPORTS: dict[str, str] = {
    "poke_host": "control.channels",
    "AgentHostDispatchRepository": "control.dispatch_repository",
    "store_final_answer": "control.final_answer",
    "host_execution_host_id": "control.host_execution",
    "host_for_host_sandbox": "control.host_execution",
    "is_paired_to_any_of": "control.host_execution",
    "choose_host_workspace": "control.host_execution_selection",
    "host_folder_for": "control.host_execution_selection",
    "recorded_host_workspace": "control.host_execution_selection",
    "settle_native_commands": "control.native_commands",
    "AgentHostOpClient": "control.ops",
    "cancel_abandoned_host_runs": "control.recovery",
    "cleanup_retained_state": "control.recovery",
    "reconcile_expired_leases": "control.recovery",
    "AgentHostRepository": "control.repository",
    "routers": "controllers",
    "AgentHostHarnessResponse": "controllers.schemas",
    "AgentHostHarnessHealth": "domain.host",
    "AgentHostStatus": "domain.host",
    "effective_agent_host_status": "domain.host",
    "HOST_OFFLINE": "domain.ops",
    "HOST_OFFLINE_MESSAGE": "domain.ops",
    "AgentHostOpError": "domain.ops",
    "validate_agent_host_model": "domain.selections",
    "validate_agent_host_selections": "domain.selections",
    "PodFileAgentHostArtifactWriter": "harness.artifacts",
    "RemoteHarness": "harness.harness",
    "host_pre_approves": "harness.pre_approvals",
    "link_registry": "link.registry",
}


def __getattr__(name: str) -> object:
    home = _EXPORTS.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{_PACKAGE}.{home}"), name)
    globals()[name] = value
    return value
