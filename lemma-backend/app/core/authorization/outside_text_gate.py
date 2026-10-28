"""The authorizer's question for an agent reading what strangers wrote.

A member's run in a group reads what people outside the pod said there, and
its conversation is marked while it does (``outside_text``). Anything such a
run does beyond reading and writing the pod's own records and files -- posting,
running a function, an agent or a workflow, using a connected account,
changing what the pod is -- waits for the person it acts for to approve it, the
way a destructive action does. Their own "add this" stays a write: it is what
they asked, it is reversible, and every row says who wrote it. Deletes are
refused before this is asked, by the destructive gate.

Agents only. A function an approved call runs is the approved action; refusing
its own steps again would leave it nowhere to ask.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.authorization.context import (
    AuthorizationDecision,
    Context,
    ResourceRef,
    WorkloadKind,
)
from app.core.authorization.outside_text import (
    OUTSIDE_TEXT_REQUIRES_APPROVAL,
    has_read_outside_text,
)
from app.core.authorization.permissions import Permissions

#: What an agent may still do unasked while it reads lines from outside the pod.
_UNGATED = frozenset({Permissions.DATASTORE_RECORD_WRITE, Permissions.FOLDER_WRITE})

#: Whether the person approved this permission for the conversation, or for
#: this one call -- the same answer the destructive gate is given.
Approved = Callable[[str], Awaitable[bool]]


async def outside_text_decision(
    ctx: Context,
    permission_id: str,
    resource: ResourceRef | None,
    *,
    approved: Approved,
) -> AuthorizationDecision | None:
    """A refusal an approval answers, or ``None`` to go on deciding."""
    delegation = ctx.delegation
    if (
        delegation is None
        or permission_id.endswith(".read")
        or permission_id in _UNGATED
        or (ctx.workload is not None and ctx.workload.kind is WorkloadKind.FUNCTION)
    ):
        return None
    if ctx._outside_text_cache is None:  # noqa: SLF001
        ctx._outside_text_cache = await has_read_outside_text(  # noqa: SLF001
            delegation.session_id
        )
    if not ctx._outside_text_cache or await approved(permission_id):  # noqa: SLF001
        return None
    return AuthorizationDecision(
        False, OUTSIDE_TEXT_REQUIRES_APPROVAL, permission_id, resource
    )
