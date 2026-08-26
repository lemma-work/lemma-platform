"""What the person said while the agent was already working.

A message sent mid-run joins the run in flight, which loaded its history before
that message existed. Nothing carried it any further, so it sat unanswered until
the person happened to send another one -- and on a chat surface, where one
message is routinely delivered as several webhooks, "another one" was often the
rest of the same sentence. Three photos and the question they were about became
one run that answered the photos and three messages nobody read.

Pydantic AI already has the seam: ``ctx.enqueue`` puts content into the run, and
the framework's own outermost capability drains it into the next model request
-- or, if the agent would otherwise finish, redirects it into one more request.
So the fix is not another run. It is telling the run that is already going.

Enqueued at ``'asap'`` (the default) and as **user content**, never as a
``SystemPromptPart``: this is a webhook payload, and a system prompt carries
operator authority that an injection buried in someone's message must not
inherit.

The hook is ``before_node_run`` rather than ``before_model_request`` on purpose.
The framework's drain is ordered outermost, so its ``before_model_request`` has
already run by the time ours would -- content enqueued there waits for the
*next* request. Enqueuing a node earlier lands it in the request about to be
built, which is what makes a burst of bubbles arrive as one turn.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import RunContext
from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger

logger = get_logger(__name__)


class PendingUserMessagesCapability(AbstractCapability[object]):
    """Steers messages that arrived mid-run into the run that is answering."""

    def __init__(self, *, agent_run_id: UUID) -> None:
        self._agent_run_id = agent_run_id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "pending_user_messages"

    async def before_node_run(self, ctx: RunContext[Any], *, node: Any) -> Any:
        """Claim anything the person has said since, and hand it to the model.

        One indexed statement per node, which is normally an update matching no
        rows -- the person is usually not typing while the agent works.

        The claim is one statement, so a database error is the whole of what it
        can fail with, and losing it must not lose the turn already in hand: the
        rows stay unclaimed and the completion backstop answers them. Anything
        else raised here is a bug and is left to surface.
        """
        try:
            messages = await self._claim()
        except SQLAlchemyError:
            logger.warning(
                "agent.pending_user_messages.claim_failed.degraded",
                agent_run_id=self._agent_run_id,
                exc_info=True,
            )
            return node
        if not messages:
            return node

        # Rendered exactly as history renders a user message, so a steered one
        # reads the same as one the run started with -- sender label, quoted
        # message, the paths of whatever they attached.
        from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
            _user_prompt_text,
        )

        for message in messages:
            ctx.enqueue(_user_prompt_text(message))
        logger.info(
            "agent.pending_user_messages.steered_into_run.observed",
            agent_run_id=self._agent_run_id,
            message_count=len(messages),
        )
        return node

    async def _claim(self) -> list[Any]:
        """Its own short unit of work: the run holds no session between nodes."""
        from app.core.infrastructure.db.session import async_session_maker
        from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
        from app.modules.agent.infrastructure.repositories import (
            ConversationRepository,
        )

        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            claimed = await ConversationRepository(uow).claim_queued_user_messages(
                self._agent_run_id
            )
            if claimed:
                await uow.commit()
            return claimed
