"""Exporting a pod's agents into a bundle's ``agents/`` directory.

Split out of :mod:`exporter` on the same grounds as :mod:`exporter_files`: the
module it came from is long enough that a reader looking for one resource type
has to skip past six others, and this one carries a rule worth reading on its
own -- see :mod:`pod_bundle.domain.exportable` for why the pod's own assistant
is never in a bundle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from lemma_pod_bundle.layout import _write_json
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _normalize_agent_payload,
)

from app.modules.pod_bundle.domain.exportable import is_exportable_agent


async def export_agents(
    uow,
    *,
    root: Path,
    pod_id: UUID,
    user_id: UUID,
    ctx: Any,
    grants_payload,
) -> None:
    """Write one directory per agent somebody made."""
    # Imported here rather than at module load: `exporter` imports this module,
    # so naming it at the top would be a cycle.
    from app.composition.pod_bundle_resources import get_agent_service
    from app.modules.pod_bundle.infrastructure.exporter import (
        _agent_response_dict,
        _extract_large_text,
    )

    agent_service = get_agent_service(uow)
    agents, _ = await agent_service.list_agents(
        pod_id=pod_id, limit=1000, requester_user_id=user_id, ctx=ctx
    )
    exportable = sorted(
        (agent for agent in agents if is_exportable_agent(agent)),
        key=lambda agent: str(agent.name or ""),
    )

    for summary in exportable:
        agent_name = str(summary.name or "")
        agent = await agent_service.get_agent_by_name(
            pod_id=pod_id,
            name=agent_name,
            requester_user_id=user_id,
            ctx=ctx,
        )
        dir_ = root / "agents" / agent_name
        dir_.mkdir(parents=True, exist_ok=True)
        payload = _normalize_agent_payload(_agent_response_dict(agent))
        grantee_id = getattr(agent, "id", None)
        if grantee_id is not None:
            grants = await grants_payload(
                uow,
                pod_id=pod_id,
                grantee_type="AGENT",
                grantee_id=grantee_id,
            )
            # Attach even an EMPTY grant list — see _resource_grants_payload
            # for why None differs from [].
            if grants is not None:
                payload = _attach_permissions_payload(payload, grants)
        payload = _extract_large_text(
            payload,
            field_name="instruction",
            file_name="instruction.md",
            resource_dir=dir_,
        )
        _write_json(dir_ / f"{agent_name}.json", payload)
