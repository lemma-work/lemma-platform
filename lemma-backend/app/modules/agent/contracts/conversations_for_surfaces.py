"""What a chat surface does to a conversation, as operations rather than a service.

The last thing `app/composition/surface_agent.py` existed to hand over was a
`ConversationService`, and handing one over is what let a delivery surface reach
a repository in another module. Twenty call sites in `agent_surfaces` went
through it, and most of them were not method calls at all -- they read a
collaborator *off* the service (`conversation_repository`, `agent_repository`,
`usage_service`, `uow`, `authorization_service`, `fallback_model_name`) and
`services/interaction_helpers.py` assembled a second service out of six of them.

Twelve operations replace it. Two of the six collaborators turned out to be
dead weight: `ConversationService` stores `authorization_service` and
`fallback_model_name` and neither it nor any of the five coordinators it builds
ever reads them -- `agent/api/dependencies.py` has never even passed a
`fallback_model_name`, so every surface was threading `None` across a module
boundary. They are still on the constructor -- taking a parameter off it is a
change to every place in `agent` that builds one, and not this change -- but
nothing crosses a module boundary for them any more.

Every operation takes the unit of work rather than being handed a bound service,
which is also what makes the worker's two modes disappear from the caller: the
ingress service used to carry both a `conversation_service` and a
`conversation_service_factory` so the long-I/O path could rebuild one against a
short session. A function taking a `uow` is the same function in both.

`agent/contracts/agents.py` covers two of the `agent_repository` reaches
outright -- `agent_name_for_id` for a raw name and `agent_id_for_name` for a
name a person typed -- and `agent/contracts/pod_summaries.py` covers the pod
listing, so the only agent lookup published here is the one that needs an
agent's *kind* to decide what a person should be shown it is called.

A submodule rather than `contracts/__init__`, which is a leaf: these reach the
service and repository layers, and everything importing any agent contract would
otherwise pay for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.api.dependencies import get_conversation_service
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.value_objects import (
    AgentRunApprovalDecision,
    MessageDraft,
)
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.conversation_retry_service import (
    ConversationRetryService,
)
from app.modules.usage.contracts.execution import build_usage_service

#: The pausing kind a surface renders as a decision rather than as a question.
#: Named here rather than imported from `pause_resume` so the published surface
#: says which kinds a caller may see, and so a new internal pausing tool does
#: not silently widen it.
_REQUEST_APPROVAL = "request_approval"


@dataclass(frozen=True, slots=True)
class SurfaceConversation:
    """A conversation as a surface needs it: who owns it, and how fresh it is.

    Six scalars rather than the `Conversation` entity the composition root used
    to hand out. The entity carries its messages and its runs, and a caller that
    is given the row reads whichever field it notices -- which is how a surface
    came to decide whether to continue a thread from `updated_at` without
    anybody choosing to publish that.
    """

    id: UUID
    user_id: UUID
    pod_id: UUID
    #: Absent for the pod's own assistant on rows written before it had one.
    agent_id: UUID | None
    title: str | None
    #: Last write to the row. A conversation has no "last spoken at"; this is
    #: the closest thing, and it is what decides whether a notification
    #: continues the thread or opens a new one.
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """A paused ``ask_user`` or ``request_approval`` awaiting a person."""

    tool_call_id: str
    #: ``"ask_user"`` or ``"request_approval"``.
    kind: str
    #: The tool call's own arguments, as stored. Unvalidated on purpose: the
    #: surface validates them against `AskUserRequest` and must be able to tell
    #: an unparseable payload from an absent one.
    tool_args: dict[str, object]
    agent_run_id: UUID | None

    @property
    def is_approval(self) -> bool:
        return self.kind == _REQUEST_APPROVAL


@dataclass(frozen=True, slots=True)
class SurfaceAgentIdentity:
    """An agent as a message header names it.

    ``is_pod_default`` rather than the raw name, because the pod's own assistant
    is stored as ``pod_default`` -- an internal identifier that used to be
    absent entirely, so every caller wrote ``or "Lemma"`` and the null did the
    work. Now that the row exists, that expression would put ``pod_default`` in
    front of a person; the flag is what lets each surface pick its own label.
    """

    id: UUID
    name: str
    is_pod_default: bool
    icon_url: str | None


def _service(uow: SqlAlchemyUnitOfWork):
    """The conversation service, from the module's own factory.

    A plain factory despite its FastAPI ``Depends`` annotation -- calling it
    directly is how the other non-request callers build the same service.
    """
    return get_conversation_service(uow)


def _pending(raw: dict[str, object] | None) -> PendingInteraction | None:
    if raw is None:
        return None
    tool_args = raw.get("tool_args")
    agent_run_id = raw.get("agent_run_id")
    return PendingInteraction(
        tool_call_id=str(raw.get("tool_call_id") or ""),
        kind=str(raw.get("kind") or ""),
        tool_args=tool_args if isinstance(tool_args, dict) else {},
        agent_run_id=agent_run_id if isinstance(agent_run_id, UUID) else None,
    )


def _conversation(entity) -> SurfaceConversation:
    return SurfaceConversation(
        id=entity.id,
        user_id=entity.user_id,
        pod_id=entity.pod_id,
        agent_id=entity.agent_id,
        title=entity.title,
        updated_at=entity.updated_at,
    )


async def surface_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> SurfaceConversation | None:
    """This conversation as a surface sees it, or ``None`` once it is gone.

    ``None`` rather than a raise: every caller is acting on a thread somebody
    left open on a phone, and a conversation deleted underneath it is an
    ordinary outcome that the surface answers in words.
    """
    entity = await ConversationRepository(uow).get_conversation(conversation_id)
    return None if entity is None else _conversation(entity)


async def open_surface_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    agent_name: str | None,
    user_id: UUID,
    title: str | None,
    metadata: dict[str, object] | None = None,
    require_execute_grant: bool = True,
) -> SurfaceConversation:
    """Start the conversation behind a surface thread.

    ``require_execute_grant=False`` is for a notification opening a thread the
    *recipient* owns: they did not ask for it, so they are not being checked for
    permission to run the agent that is writing to them. Every inbound path
    leaves it on.

    The caller sets the authorization context, because it is the caller that
    knows whose it is -- an inbound message runs as the sender, a notification
    as nobody in particular.
    """
    conversation = await _service(uow).create_conversation(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=title,
        metadata=metadata,
        require_execute_grant=require_execute_grant,
    )
    return _conversation(conversation)


async def start_surface_turn(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID | None,
    user_id: UUID,
    pod_id: UUID,
    content: str,
    agent_name: str | None = None,
    message_metadata: dict[str, object] | None = None,
) -> UUID | None:
    """Record an inbound message and start the run that answers it.

    Returns the run's id, or ``None`` if no new run was needed -- a message
    arriving while one is already going is handed to that run rather than
    starting a second.
    """
    result = await _service(uow).add_user_message_and_start_run(
        conversation_id=conversation_id,
        user_id=user_id,
        content=content,
        pod_id=pod_id,
        agent_name=agent_name,
        message_metadata=message_metadata,
    )
    return result.agent_run_id if result.started_new_run else None


async def append_notification_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    message: str,
    notification_id: UUID,
) -> None:
    """Write an outbound notification into the recipient's thread.

    Belongs to no run, which is the point: it is what makes the recipient's
    agent able to answer "yes" six hours later against a question it can
    actually see in its own history.
    """
    await ConversationRepository(uow).append_message(
        conversation_id=conversation_id,
        agent_run_id=None,
        draft=MessageDraft.of_notification(
            message, metadata={"notification_id": str(notification_id)}
        ),
    )


async def pending_interaction(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved pause of any kind, or ``None``.

    "What is this conversation waiting on" -- the right question for routing a
    typed reply back, because a person answering in words answers whatever is
    pending. It is the wrong question for rendering a card: see
    :func:`pending_approval`.
    """
    return _pending(
        await _service(uow).get_pending_user_interaction(
            conversation_id=conversation_id
        )
    )


async def pending_question(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved ``ask_user``, or ``None``."""
    return _pending(
        await _service(uow).get_pending_ask_user(conversation_id=conversation_id)
    )


async def pending_approval(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved ``request_approval``, or ``None``.

    Its own lookup rather than a filter over :func:`pending_interaction`: a
    conversation can hold several unresolved pauses at once, and an `ask_user`
    nobody ever tapped stays unresolved forever. Being older, it was the one
    returned -- so the approval card had nothing to render and the run sat
    WAITING with nobody told.
    """
    return _pending(
        await _service(uow).get_pending_approval(conversation_id=conversation_id)
    )


async def resolve_pending_interaction(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    approval_id: str,
    user_id: UUID,
    pod_id: UUID,
    decision: AgentRunApprovalDecision,
    response: dict[str, object] | None = None,
    defer_reconciliation: bool = True,
) -> bool:
    """Record a person's decision and resume the run it paused.

    ``False`` when the conversation is no longer there, and nothing was
    recorded. The caller must be able to tell that apart from a decision that
    landed: starting an ordinary turn instead supersedes the pause with an
    auto-denial, which is how an "approve" became a cancellation.

    Skips the authorization the HTTP entry point does, exactly as the service
    method it replaces did: a surface has already matched the person who tapped
    against the conversation's owner, and it is holding a webhook deadline while
    it does. ``defer_reconciliation`` is on by default for the same reason --
    an approved command outlives the webhook that approved it.
    """
    entity = await ConversationRepository(uow).get_conversation(conversation_id)
    if entity is None:
        return False
    await _service(uow).resolve_user_approval_internal(
        conversation=entity,
        approval_id=approval_id,
        user_id=user_id,
        pod_id=pod_id,
        decision=decision,
        response=response or {},
        defer_reconciliation=defer_reconciliation,
    )
    return True


async def retry_failed_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None = None,
) -> None:
    """Run the conversation's last failed turn again.

    Raises rather than reporting: the two callers want opposite things from a
    failure -- a tapped Retry button says "I couldn't complete that action" and
    a ``/retry`` command says there is nothing safe to retry -- and only they
    know which errors mean which.

    Sets the owner's authorization context for the duration, because the retry
    re-authorizes the conversation it is restarting and the surface that asked
    for it is not running as anybody. This is the whole of what
    `agent_surfaces` used to build a second `ConversationService` for.
    """
    auth_ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(auth_ctx)
    try:
        await ConversationRetryService(
            uow=uow,
            conversation_repository=ConversationRepository(uow),
            agent_repository=AgentRepository(uow),
            authorization_service=create_authorization_data_service(uow),
            usage_service=build_usage_service(uow),
        ).retry_failed_run(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
    finally:
        reset_current_context(token)


async def surface_agent_identity(
    uow: SqlAlchemyUnitOfWork, agent_id: UUID
) -> SurfaceAgentIdentity | None:
    """How to name this agent in front of a person, or ``None`` if it is gone.

    Three columns, not the agent row: `agent_surfaces` was loading the whole
    entity through `agent_repository.get` to read a name, a kind and an icon.
    """
    row = (
        await uow.session.execute(
            select(
                AgentModel.id, AgentModel.name, AgentModel.kind, AgentModel.icon_url
            ).where(AgentModel.id == agent_id)
        )
    ).one_or_none()
    if row is None:
        return None
    identifier, name, kind, icon_url = row
    return SurfaceAgentIdentity(
        id=identifier,
        name=name,
        is_pod_default=AgentKind(kind) is AgentKind.POD_DEFAULT,
        icon_url=icon_url,
    )


async def conversation_metadata_value(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, key: str
) -> str | None:
    """One string this conversation's metadata is carrying, if any.

    The read half of the pair `agent/contracts/conversations.py` publishes the
    other side of. Generic rather than named for what a surface stores there,
    because the row is agent's and the meaning of the key is the caller's:
    `agent_surfaces` remembers which paused call somebody tapped "Other" on,
    the agent host keeps its session memory, and `todo_storage` its list.

    ``None`` for anything that is not a string, so a caller comparing an id
    cannot be fooled by a value of another shape written under the same key.
    """
    stored = await ConversationRepository(uow).get_conversation_metadata_key(
        conversation_id, key
    )
    return stored if isinstance(stored, str) else None


async def set_conversation_metadata_value(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, key: str, value: str | None
) -> None:
    """Write one metadata key, leaving every sibling key alone.

    Not `merge_conversation_metadata`: that one reads the column, edits it and
    writes it back, so a concurrent writer touching another key loses. This
    goes through `jsonb_set`, which is what a flag written from an inbound
    webhook while a run is stamping its own metadata needs.
    """
    await ConversationRepository(uow).set_conversation_metadata_key(
        conversation_id, key, value
    )


__all__ = [
    "PendingInteraction",
    "SurfaceAgentIdentity",
    "SurfaceConversation",
    "append_notification_message",
    "conversation_metadata_value",
    "open_surface_conversation",
    "pending_approval",
    "pending_interaction",
    "pending_question",
    "resolve_pending_interaction",
    "retry_failed_run",
    "set_conversation_metadata_value",
    "start_surface_turn",
    "surface_agent_identity",
    "surface_conversation",
]
