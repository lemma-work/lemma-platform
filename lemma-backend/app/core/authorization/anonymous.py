"""The authority of somebody a pod does not know.

A person outside a pod who writes to one of its bots has no account the pod can
grant anything to, and no role in it. The run that answers them still has to be
authorized as *somebody*, and the honest somebody is nobody:
``ActorType.ANONYMOUS``, which the authorizer answers with reads of Public
resources and nothing else -- in ``Authorizer.authorize`` and in the SQL
projection alike.

Pinned to one pod. The anonymous branch used to ask only whether a resource was
Public, which was all it needed while nothing could build this context. A run
answering an outsider is one pod's bot, and a Public page in another pod is not
its to quote, so ``pod_id`` here is checked against the resource's own pod.

No database read, deliberately. The organization comes from the caller, which
already holds it (a run's context carries ``org_id``), and a lookup here would
make ``app/core`` read a module's table to build a context that grants nothing.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.authorizer import Authorizer
from app.core.authorization.context import ActorType, Context


def build_anonymous_context(
    *,
    session: AsyncSession,
    pod_id: UUID,
    organization_id: UUID | None,
    actor_id: str,
    request_id: str | None = None,
) -> Context:
    """A context that may read what ``pod_id`` has marked Public, and nothing else.

    ``actor_id`` names who is acting for the audit trail -- an outsider's
    conversation, say -- and grants nothing: there are no principals on this
    context for any grant to match.
    """
    return Context(
        actor_type=ActorType.ANONYMOUS,
        actor_id=actor_id,
        authorizer=Authorizer(session),
        request_id=request_id,
        organization_id=organization_id,
        pod_id=pod_id,
    )
