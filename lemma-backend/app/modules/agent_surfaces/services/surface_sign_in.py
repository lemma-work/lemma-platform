"""Asking a person to sign in to a site, on whichever surface they are on.

A link, not buttons. The approval path offers Approve and Deny, and neither is
an answer to "please go to this page and sign in" -- rendering it that way would
put a question in front of somebody that nobody meant to ask.

Without this the promise that an agent "reaches the person wherever they are"
was not met at all. The run paused correctly and waited indefinitely, and the
only way to find out was to already be looking at the conversation: nothing
anywhere composed the URL.

A function rather than another mixin, because building the message needs only a
unit of work, while *delivering* it needs the egress target machinery. Keeping
the two apart is what lets this be read, and tested, without a surface.
"""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent_surfaces.domain.envelope import SurfaceEnvelope
from app.modules.agent_surfaces.platforms.rendering import sanitize_user_visible_text


async def sign_in_prompt_envelope(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    tool_call_id: str | None,
    narration: str | None = None,
) -> SurfaceEnvelope | None:
    """The message that sends somebody to sign in, or ``None`` if there is none.

    Keyed on the tool call, which is the durable link between the paused run and
    the row. "The newest open request for this person" would hand somebody the
    wrong site whenever two runs are waiting at once.
    """
    if not tool_call_id:
        return None

    from app.modules.web_login.contracts import SignInRequestRepository

    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    if conversation is None:
        return None
    request = await SignInRequestRepository(uow.session).for_tool_call(
        conversation.user_id, tool_call_id
    )
    if request is None:
        return None

    lines = [f"I need you to sign in to {request.origin} so I can carry on."]
    if request.reason:
        lines.append(f"What I am doing: {sanitize_user_visible_text(request.reason)}")
    lines.append(f"{settings.frontend_url.rstrip('/')}/sign-in-to-site/{request.id}")
    lines.append(
        "The link opens the site in my browser for you. I will not ask for your "
        "password and I cannot see it."
    )

    # One message, like the question path: a lead-in sent separately arrives as a
    # second message on chat and a second email on email.
    body = "\n".join(lines)
    return SurfaceEnvelope(text="\n\n".join(part for part in [narration, body] if part))


__all__ = ["sign_in_prompt_envelope"]
