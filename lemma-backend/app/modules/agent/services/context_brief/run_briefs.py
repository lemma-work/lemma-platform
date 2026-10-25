"""The briefs one run's prompt carries, set on its tool context.

Which brief a run gets depends on whom it answers: a linked pod gets the link
brief, a contact the outsider brief, and everyone else the agent's own context
brief (resources, memory, self, grants, askable pods). The attached document,
the open questions and the task list follow, rendered into the run's context.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.capabilities.open_questions import open_question_text
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.run_context_prompt import (
    render_run_context,
    task_list_section,
)
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services.attached_document_brief import (
    build_attached_document_section,
)
from app.modules.agent.services.brief_lines import run_trigger_of
from app.modules.agent.services.link_brief import link_brief
from app.modules.agent.services.outsider_brief import outsider_brief
from app.modules.agent.tools.context import ConversationContext

from uuid import UUID

logger = get_logger(__name__)

_CONTEXT_BRIEF_UNAVAILABLE = (
    "## Runtime Context\n\n"
    "This section could not be assembled for this run, so the pod's tables, "
    "agents, files and your memory are not listed here. They still exist -- "
    "list them with your tools rather than concluding the pod is empty."
)


async def run_briefs(
    ctx: ConversationContext,
    uow_factory: UnitOfWorkFactory,
    *,
    conversation: Conversation,
    agent: Agent,
    agent_run: AgentRun,
    user_id: UUID,
    run_toolsets: list[AgentToolset],
    platform: str | None,
    grant_rows: list[tuple[str, UUID, str]] | None = None,
) -> None:
    """Set the run's brief, attached document, open questions and run context."""
    run_brief = await _brief(
        ctx,
        uow_factory,
        conversation=conversation,
        agent=agent,
        agent_run=agent_run,
        user_id=user_id,
        run_toolsets=run_toolsets,
        platform=platform,
        grant_rows=grant_rows,
    )
    try:
        ctx.attached_document = await build_attached_document_section(
            uow_factory,
            conversation=conversation,
            pod_id=conversation.pod_id,
            user_id=user_id,
        )
    # Same survivable failures as the brief: without the section the agent
    # reads the doc itself, which is slower but not wrong.
    except DomainError, SQLAlchemyError, OSError, TimeoutError:
        logger.warning(
            "agent.run.attached_document_unavailable.degraded",
            conversation_id=str(conversation.id),
            exc_info=True,
        )
    # Never on a stranger's run: an open request is the owner's to answer.
    if not ctx.audience.answers_outsiders:
        ctx.open_questions = await open_question_text(
            conversation_id=conversation.id,
            channel_id=getattr(conversation, "channel_id", None),
            pod_id=conversation.pod_id,
            user_id=conversation.user_id,
        )
    ctx.run_context = render_run_context(
        sections=(
            run_brief,
            ctx.attached_document,
            task_list_section(conversation, toolsets=run_toolsets),
            ctx.open_questions,
        )
    )


async def _brief(
    ctx: ConversationContext,
    uow_factory: UnitOfWorkFactory,
    *,
    conversation: Conversation,
    agent: Agent,
    agent_run: AgentRun,
    user_id: UUID,
    run_toolsets: list[AgentToolset],
    platform: str | None,
    grant_rows: list[tuple[str, UUID, str]] | None,
) -> str:
    """Set the stable brief on ``ctx``; return the per-run part."""
    audience = ctx.audience
    try:
        if audience.asking_pod_id is not None:
            ctx.context_brief = await link_brief(
                uow_factory,
                pod_id=conversation.pod_id,
                asking_pod_id=audience.asking_pod_id,
                steward_user_id=user_id,
            )
            return ""
        if audience.answers_outsiders:
            ctx.context_brief = await outsider_brief(
                uow_factory,
                pod_id=conversation.pod_id,
                owner_user_id=user_id,
                contact_id=audience.contact_id,
            )
            return ""
        brief = await AgentContextBriefBuilder(uow_factory).build(
            agent=agent,
            conversation=conversation,
            toolsets=run_toolsets,
            user_id=user_id,
            pod_id=conversation.pod_id,
            # What started *this* run, as against how the conversation began.
            # A schedule stamps the conversation once and it stays stamped, so
            # only the run can say whether a person is here on this turn.
            run_trigger=run_trigger_of(agent_run),
            platform=platform,
            # Read once by the run's session, for its toolsets and this brief.
            grant_rows=grant_rows,
        )
        ctx.context_brief = brief.stable
        return brief.per_run
    # The failures a run should survive: a denied grant, a missing file, a
    # database or storage blip. Not a TypeError -- a real authorization failure
    # "should fail the brief, not hide as an empty memory section". A run could
    # lose the pod's name, its tables, its grants and its memory at once, with
    # nothing logged and nothing said to the model, which then saw a pod that
    # appeared to be empty.
    except (DomainError, SQLAlchemyError, OSError, TimeoutError) as exc:
        raise_if_database_unavailable(exc)
        logger.warning(
            "agent.run.context_brief_unavailable.degraded",
            agent_id=str(agent.id),
            conversation_id=str(conversation.id),
            exc_info=True,
        )
        ctx.context_brief = _CONTEXT_BRIEF_UNAVAILABLE
        return ""
