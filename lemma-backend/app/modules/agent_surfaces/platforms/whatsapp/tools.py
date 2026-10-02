"""The one tool a member's private WhatsApp chat adds: opening a group.

Offered only there. In a group it would let whoever is talking open more groups
in a member's name, and a run answering somebody outside the pod gets no
platform tools at all (see ``agent/tools/tool_assembler``).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result
from app.modules.agent_surfaces.platforms.whatsapp.client import (
    GROUP_SUBJECT_MAX_CHARS,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.services.whatsapp_groups import (
    GroupOpenLimitReached,
    GroupsNotAvailable,
    OpenedGroup,
    WhatsAppGroupOpener,
    may_configure_bot,
)


class OpenWhatsAppGroupParams(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=GROUP_SUBJECT_MAX_CHARS,
        description=(
            "The group's name as people will see it, e.g. 'Acme x Northwind "
            "order 1182'. Asking again with the same name returns the same "
            "group rather than a new one."
        ),
    )


class OpenWhatsAppGroupResult(BaseModel):
    success: bool
    group: OpenedGroup | None = None
    error: str | None = None


def build_whatsapp_group_toolset(
    *, uow_factory: UnitOfWorkFactory, surface_id: UUID
) -> FunctionToolset[ConversationContext]:
    opener = WhatsAppGroupOpener(uow_factory)

    async def whatsapp_open_group(
        ctx: RunContext[ConversationContext],
        request: OpenWhatsAppGroupParams,
    ) -> OpenWhatsAppGroupResult:
        """Open a WhatsApp group with this bot in it, for the person you are talking to.

        Use when they want to talk with someone outside the pod -- a client, a
        vendor -- with you in the conversation. They become the person who
        answers for the group: people there who are not in the pod are answered
        from what the pod has marked Public, and anything more is passed to them.

        Returns an invite link for them to share. A group holds at most eight
        people. You answer there only when somebody addresses you by name, by
        @-mentioning you, or by replying to you. If `pending` is true WhatsApp
        has not confirmed the group yet: say the link is on its way, and call
        this again with the same title in a minute to get it.
        """
        return await guarded_tool_result(
            _open(opener, ctx=ctx, surface_id=surface_id, title=request.title),
            tool="whatsapp_open_group",
            failure=OpenWhatsAppGroupResult(
                success=False,
                error="WhatsApp would not open the group. Try again shortly.",
            ),
        )

    return FunctionToolset[ConversationContext](tools=[whatsapp_open_group])


async def _open(
    opener: WhatsAppGroupOpener,
    *,
    ctx: RunContext[ConversationContext],
    surface_id: UUID,
    title: str,
) -> OpenWhatsAppGroupResult:
    async with opener.uow_factory() as uow:
        surface = await SurfaceRepository(uow).get(surface_id)
        allowed = surface is not None and await may_configure_bot(
            uow, user_id=ctx.deps.user_id, surface=surface
        )
    if not allowed:
        return OpenWhatsAppGroupResult(
            success=False,
            error=(
                "Only someone who can change this bot's settings can open groups "
                "with it. Ask an admin of the space."
            ),
        )
    try:
        group = await opener.open(
            surface_id=surface_id, owner_user_id=ctx.deps.user_id, title=title
        )
    except (GroupOpenLimitReached, GroupsNotAvailable) as refused:
        return OpenWhatsAppGroupResult(success=False, error=str(refused))
    return OpenWhatsAppGroupResult(success=True, group=group)
