"""A finished run's answer, said at the place of what it answered.

The one way any run's answer is said (``services/run_answers``), published
for the surfaces' drain of what was queued before it was the one way.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.contracts import ChannelMessage


async def post_run_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    answered: bool = True,
) -> ChannelMessage | None:
    """The run's answer -- or, in Lemma, why there is none -- once."""
    from app.modules.agent.services.run_answers import (
        post_run_answer as post_answer,
    )

    return await post_answer(
        uow, run_id=run_id, conversation_id=conversation_id, answered=answered
    )


__all__ = ["post_run_answer"]
