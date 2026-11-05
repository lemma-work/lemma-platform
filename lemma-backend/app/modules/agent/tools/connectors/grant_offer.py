"""A named agent refused a pod connector asks to be given it.

The pod's assistant reaches a person's apps with no setup. A named agent does
not: it needs ``connector.use`` on the pod connector, and until now being
refused it was a dead end the model could only report -- the fix was a CLI
command nobody in the conversation was going to run.

So the refusal becomes a card the person can answer: "Let triage use gmail
(your own account)?". The offer is written down here, by the server, keyed by
the exact call like the denial beside it, and carried on the card when it is
shown (``approval_denials.show_card``). Approving adds the grant, standing,
conferred as the approver (``approval/grant_outcome``).

Only this refusal: a missing workload grant of ``connector.use`` on a pod
connector. The person's own ceiling, a disabled installation, an account that
needs connecting -- none of those is the agent's grant to give.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.authorization.permissions import Permissions
from app.core.authorization.session_approvals import record_grant_offer
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.tools.approval_denials import remember_denial
from app.modules.agent.tools.authority import workload_actor_id
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.connectors.contracts.pod_connectors import get_pod_connector

_REFUSAL = "MISSING_WORKLOAD_RESOURCE_GRANT"


def _is_connector_grant_refusal(exc: DomainError) -> bool:
    details = exc.details if isinstance(exc.details, dict) else {}
    permission_ids = details.get("permission_ids")
    return (
        exc.code == _REFUSAL
        and details.get("resource_type") == "connector"
        and isinstance(permission_ids, list)
        and Permissions.CONNECTOR_USE in permission_ids
    )


def grant_card_title(agent: str, connector: str, *, per_member: bool) -> str:
    """The card's question, in the person's terms: who, which app, whose account."""
    whose = "your own account" if per_member else "the account shared with the pod"
    return f"Let {agent} use {connector} ({whose})?"


async def connector_grant_offer(
    deps: BaseAgentContext,
    exc: DomainError,
    *,
    connector: str | None,
    tool_name: str,
    args: dict[str, Any],
    auth_config_id: UUID | None = None,
) -> dict[str, Any] | None:
    """The ``needs_approval`` result that asks to give the agent this connector,
    or None when ``exc`` is not that refusal (the caller reports it as is).

    ``auth_config_id`` is the person's own installation the refused call would
    have declared as a connector: the declaration was rolled back with the
    refusal, so the offer names the installation and approving declares it.
    """
    if (
        not _is_connector_grant_refusal(exc)
        or deps.is_pod_default_agent
        or deps.workload_id is None
        or deps.pod_id is None
        or not connector
    ):
        return None
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        pod_connector = await get_pod_connector(uow, pod_id=deps.pod_id, slug=connector)
    if pod_connector is not None:
        per_member = pod_connector.mode.value == "PER_MEMBER"
        target = {"pod_connector_id": str(pod_connector.id)}
    elif auth_config_id is not None:
        # Declared on approval, as a connector of each member's own account.
        per_member = True
        target = {"auth_config_id": str(auth_config_id)}
    else:
        return None
    agent = deps.agent_name or "this agent"
    await record_grant_offer(
        session_id=str(deps.conversation_id),
        workload_actor_id=workload_actor_id(deps),
        tool_name=tool_name,
        args=args,
        offer={
            "kind": Permissions.CONNECTOR_USE,
            "pod_id": str(deps.pod_id),
            **target,
            "connector": connector,
            "per_member": per_member,
        },
    )
    result: dict[str, Any] = {
        "success": False,
        "error": (
            f"You have not been given the {connector} connector. Ask your person "
            "with request_approval, copying `approval` below: approving gives you "
            f"{connector} from now on, not only for this call."
        ),
        "code": exc.code,
        "connector": connector,
        "needs_approval": True,
        "approval": {
            "tool_name": tool_name,
            "args": args,
            "reason_code": exc.code,
            "permission_ids": [Permissions.CONNECTOR_USE],
            "title": grant_card_title(agent, connector, per_member=per_member),
        },
    }
    await remember_denial(deps, result)
    return result
