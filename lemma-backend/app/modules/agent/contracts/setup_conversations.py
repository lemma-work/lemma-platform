"""The chat a new Lemma sets itself up in, opened by the person who started it.

``pod_bundle`` starts a Lemma in one transaction: the pod, what it owns, its
plan, and this -- a conversation with the pod's own agent whose first message
is the person's, asking it to set itself up. It is an ordinary chat they land
in and watch, opened in the caller's transaction so a failure anywhere leaves
no conversation behind.

A Lemma started from a template opens it *held*: the run is written but not
its turn, because the template's tables and schedules are applied by a worker
after the commit, and the agent must not start on a pod that is half there.
Whatever applies the template starts the turn with ``start_setup_turn`` when it
is done.

A submodule rather than ``contracts/__init__``: it reaches the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.turns import AgentTurnStart, stage_agent_turn


@dataclass(frozen=True, slots=True)
class SetupConversation:
    """The setup chat, and the run that answers its first message."""

    conversation_id: UUID
    run_id: UUID
    pod_id: UUID
    user_id: UUID


async def open_setup_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    user_id: UUID,
    text: str,
    held: bool,
) -> SetupConversation:
    """Open the chat with ``text`` as ``user_id``'s first message; no commit.

    For the person who has just made the pod, so its own agent's run is theirs
    to start without a further grant -- they administer the pod. ``held``
    writes the run without its turn (see the module docstring).
    """
    from app.modules.agent.services.service_factories import (
        build_conversation_service,
    )

    service = build_conversation_service(uow)
    conversation = await service.create_conversation(
        pod_id=pod_id,
        agent_name=None,
        user_id=user_id,
        require_execute_grant=False,
    )
    started = await service.turns.start(
        conversation,
        user_id=user_id,
        pod_id=pod_id,
        content=text,
        agent_name=None,
        message_metadata=None,
        typed_in_lemma=True,
        stage_turn=not held,
        commit=False,
    )
    return SetupConversation(
        conversation_id=started.conversation_id,
        run_id=started.agent_run_id,
        pod_id=pod_id,
        user_id=user_id,
    )


async def start_setup_turn(uow: SqlAlchemyUnitOfWork, setup: SetupConversation) -> None:
    """Start a held setup chat's first turn, in ``uow``'s transaction.

    Staging is keyed by the run, so a second call while the first turn is
    still queued adds nothing; the turn itself settles a run that has already
    ended -- stopped by the person while it was held -- without running it.
    """
    await stage_agent_turn(
        uow,
        AgentTurnStart(
            conversation_id=setup.conversation_id,
            run_id=setup.run_id,
            pod_id=setup.pod_id,
            user_id=setup.user_id,
        ),
    )


__all__ = ["SetupConversation", "open_setup_conversation", "start_setup_turn"]
