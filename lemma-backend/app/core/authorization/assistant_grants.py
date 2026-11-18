"""A grant to run the pod's own assistant, stored where it is checked.

Starting the assistant is authorized against the pod, not against its row
(`agent.services.conversation_access`): the row's id is the pod's, and an
AGENT-typed check would hand the resource-owner shortcut to whoever made the
pod. So the grant that lets a named function or agent start it is the pod's
`agent.execute` -- `{"resource_type": "pod", "resource_name": "/"}`, the grant a
pod link already holds -- and it is still bounded by the person the run acts
for, who must be able to run the assistant themselves.

`agent:pod_default:execute`, which names the row, was accepted and then never
matched. It is stored as the pod's grant instead, so the spelling a builder
reaches for does what it says. Anything else named on the assistant's row is
left as it was.
"""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.authorization.context import ResourceType
from app.core.authorization.permissions import Permissions

_RUN_THE_ASSISTANT = frozenset({Permissions.AGENT_EXECUTE, Permissions.AGENT_READ})


def runs_the_assistant(
    resource_type: ResourceType,
    resource_id: UUID,
    permission_ids: Collection[str],
    *,
    pod_id: UUID,
) -> bool:
    """Whether this grant, on the assistant's row, asks only to run it."""
    return (
        resource_type is ResourceType.AGENT
        and resource_id == pod_id
        and set(permission_ids) <= _RUN_THE_ASSISTANT
        and Permissions.AGENT_EXECUTE in permission_ids
    )


__all__ = ["runs_the_assistant"]
