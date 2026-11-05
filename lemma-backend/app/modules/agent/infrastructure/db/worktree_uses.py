"""Which of a person's conversations work in a repository worktree, and how.

The read the worktree sweep weighs (``domain/worktree_sweep``). One sandbox
per person and workspace holds every worktree they have, whichever pod the
conversation is in, so this reads across pods: by the person, and by the
workspace the same way ``resolve_workspace_location`` picks it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.modules.agent.domain.project_repo import WORKTREES_ROOT
from app.modules.agent.domain.value_objects import ACTIVE_AGENT_RUN_STATUSES
from app.modules.agent.domain.worktree_sweep import WorktreeUse
from app.modules.agent.infrastructure.db.base import UnitOfWorkRepository
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
)

_ACTIVE = [status.value for status in ACTIVE_AGENT_RUN_STATUSES]


class WorktreeUses(UnitOfWorkRepository):
    async def of_person(self, user_id: UUID, *, workspace_id: str) -> list[WorktreeUse]:
        conversation = ConversationModel
        running = (
            select(AgentRunModel.id)
            .where(
                AgentRunModel.conversation_id == conversation.id,
                AgentRunModel.status.in_(_ACTIVE),
            )
            .exists()
        )
        workspace = func.coalesce(
            conversation.workspace_id, conversation.workspace_name, "default"
        )
        rows = await self.session.execute(
            select(
                conversation.cwd,
                conversation.is_archived,
                conversation.last_activity_at,
                running,
            ).where(
                conversation.user_id == user_id,
                conversation.cwd.startswith(f"{WORKTREES_ROOT}/", autoescape=True),
                workspace == workspace_id,
            )
        )
        return [
            WorktreeUse(
                cwd=cwd,
                archived=bool(archived),
                last_activity_at=last_activity_at,
                running=bool(is_running),
            )
            for cwd, archived, last_activity_at, is_running in rows
        ]
