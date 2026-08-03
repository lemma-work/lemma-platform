"""Reads and writes for ``agent_conversation_waits``.

Split out of ``repositories.py``: the waits table is its own aggregate with its
own lifecycle (claim under a lock, sweep past-due rows), and the module it came
from is already twice the architecture ratchet's file-size limit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.wait import (
    AgentConversationWaitEntity,
    AgentWaitStatus,
)
from app.modules.agent.infrastructure.models import AgentConversationWaitModel


class AgentConversationWaitRepository:
    """Reads and writes for ``agent_conversation_waits``.

    Deliberately narrow: create, resolve by external ref, claim under a lock,
    and list past-due rows for the sweep. Anything richer belongs in a service —
    this table exists to answer "what is this conversation waiting on", and a
    repository that grows opinions about *why* is how the workflow wait table
    stayed clean and this one would not.
    """

    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.uow = uow
        self.session = uow.session

    async def create(
        self, wait: AgentConversationWaitEntity
    ) -> AgentConversationWaitEntity:
        model = AgentConversationWaitModel(
            id=wait.id,
            created_at=wait.created_at,
            updated_at=wait.updated_at,
            conversation_id=wait.conversation_id,
            agent_run_id=wait.agent_run_id,
            pod_id=wait.pod_id,
            tool_call_id=wait.tool_call_id,
            wait_type=wait.wait_type.value,
            status=wait.status.value,
            external_ref=wait.external_ref,
            scheduled_at=wait.scheduled_at,
            wake_attempts=wait.wake_attempts,
            spec=dict(wait.spec or {}),
        )
        self.session.add(model)
        await self.session.flush()
        return model.to_entity()

    async def get(self, wait_id: UUID) -> AgentConversationWaitEntity | None:
        model = await self.session.get(AgentConversationWaitModel, wait_id)
        return model.to_entity() if model else None

    async def find_active_by_external_ref(
        self, external_ref: str
    ) -> AgentConversationWaitEntity | None:
        stmt = select(AgentConversationWaitModel).where(
            AgentConversationWaitModel.external_ref == external_ref,
            AgentConversationWaitModel.status == AgentWaitStatus.ACTIVE.value,
        )
        model = (await self.session.execute(stmt)).scalars().first()
        return model.to_entity() if model else None

    async def find_active_for_conversation(
        self, conversation_id: UUID
    ) -> AgentConversationWaitEntity | None:
        stmt = select(AgentConversationWaitModel).where(
            AgentConversationWaitModel.conversation_id == conversation_id,
            AgentConversationWaitModel.status == AgentWaitStatus.ACTIVE.value,
        )
        model = (await self.session.execute(stmt)).scalars().first()
        return model.to_entity() if model else None

    async def claim(self, wait_id: UUID) -> AgentConversationWaitEntity | None:
        """Lock the row and hand it back only if it is still ACTIVE.

        The timer wake and the reconciliation sweep can both fire for the same
        wait; whichever gets the lock second sees a non-ACTIVE row and stops.
        """
        stmt = (
            select(AgentConversationWaitModel)
            .where(AgentConversationWaitModel.id == wait_id)
            .with_for_update(skip_locked=False)
        )
        model = (await self.session.execute(stmt)).scalars().first()
        if model is None or model.status != AgentWaitStatus.ACTIVE.value:
            return None
        return model.to_entity()

    async def update(
        self, wait: AgentConversationWaitEntity
    ) -> AgentConversationWaitEntity | None:
        model = await self.session.get(AgentConversationWaitModel, wait.id)
        if model is None:
            return None
        model.status = wait.status.value
        model.spec = dict(wait.spec or {})
        model.completed_at = wait.completed_at
        model.wake_attempts = wait.wake_attempts
        await self.session.flush()
        return model.to_entity()

    async def record_wake_attempt(self, wait_id: UUID) -> int:
        """Increment the attempt counter and return the new value.

        Called in a transaction of its own, *before* the wake it counts — a wake
        that raises rolls back everything it touched, so a counter bumped inside
        it would roll back too and the wait would retry forever.
        """
        model = await self.session.get(AgentConversationWaitModel, wait_id)
        if model is None:
            return 0
        model.wake_attempts = (model.wake_attempts or 0) + 1
        await self.session.flush()
        return model.wake_attempts

    async def list_active_due(
        self, *, due_before: datetime, limit: int = 100
    ) -> list[AgentConversationWaitEntity]:
        """ACTIVE waits already overdue by the caller's grace period.

        Feeds the sweep that self-heals a lost scheduler event, so ``due_before``
        is deliberately in the past: a wait that merely reached ``scheduled_at``
        is being woken by its own timer right now, and sweeping it there would
        race the wake it is supposed to be a backstop for. A wait legitimately
        scheduled into the future is left alone either way.
        """
        stmt = (
            select(AgentConversationWaitModel)
            .where(
                AgentConversationWaitModel.status == AgentWaitStatus.ACTIVE.value,
                AgentConversationWaitModel.scheduled_at <= due_before,
            )
            .order_by(AgentConversationWaitModel.created_at)
            .limit(limit)
        )
        models = (await self.session.execute(stmt)).scalars().all()
        return [model.to_entity() for model in models]
