"""The authority of somebody outside a pod: an anonymous visitor or a contact.

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

A contact is the same person with a name: the pod knows them by a handle a
channel or a code vouched for. They read what an anonymous visitor reads, and
``contact_id`` on the context is what the pod database narrows contact-owned
tables by -- every pod session opened for this context names it.

Every outside principal is built here, whichever door the person came in by
(a web visitor's token, a vouched-for WhatsApp, Telegram or email handle), so
"who is this outsider" has one answer.

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


def build_outsider_context(
    *,
    session: AsyncSession,
    pod_id: UUID,
    organization_id: UUID | None,
    contact_id: UUID | None,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> Context:
    """The context of somebody outside ``pod_id``: a contact, or nobody.

    ``CONTACT`` when ``contact_id`` names one, ``ANONYMOUS`` otherwise. Neither
    holds a principal any grant could match. ``actor_id`` defaults to
    ``contact:{id}`` for a contact; an anonymous caller must name itself.
    """
    if contact_id is None:
        if actor_id is None:
            raise ValueError("An anonymous context must name who is acting")
        return build_anonymous_context(
            session=session,
            pod_id=pod_id,
            organization_id=organization_id,
            actor_id=actor_id,
            request_id=request_id,
        )
    return Context(
        actor_type=ActorType.CONTACT,
        actor_id=actor_id or f"contact:{contact_id}",
        authorizer=Authorizer(session),
        request_id=request_id,
        organization_id=organization_id,
        pod_id=pod_id,
        contact_id=contact_id,
    )
