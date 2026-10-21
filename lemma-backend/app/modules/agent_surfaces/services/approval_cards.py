"""The card a person approves a paused ``request_approval`` on: the agent's
own title and reason, with a redacted look at the call it wants run."""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversations_for_surfaces import (
    PendingInteraction,
)
from app.modules.agent_surfaces.domain.models import SurfaceApprovalRenderPlan
from app.modules.agent_surfaces.services.approval_preview import (
    approval_action_summary,
    redact_card_text,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
)


async def approval_plan(
    uow: SqlAlchemyUnitOfWork,
    pending: PendingInteraction,
    conversation_id: UUID,
    tool_call_id: str | None,
) -> SurfaceApprovalRenderPlan:
    """The approval card for a paused ``request_approval`` call."""
    callback_id = pending.tool_call_id or str(tool_call_id or "")
    tool_args = pending.tool_args
    # An approve-for-session button only makes sense when the paused call
    # carries a real permission gate (it lets the exact action skip future
    # prompts); otherwise it is noise.
    permission_ids = tool_args.get("permission_ids")
    return build_approval_render_plan(
        conversation_id=conversation_id,
        tool_call_id=callback_id,
        title=redact_card_text(
            str(tool_args.get("title") or "Action requires your approval")
        ),
        reason=redact_card_text(str(tool_args.get("reason") or "")) or None,
        tool_name=approval_action_summary(
            str(tool_args.get("tool_name") or ""), tool_args.get("args")
        ),
        allow_session=bool(isinstance(permission_ids, list) and permission_ids),
    )
