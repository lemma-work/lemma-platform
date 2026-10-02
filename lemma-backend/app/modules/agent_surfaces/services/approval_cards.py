"""The card a person approves a paused ``request_approval`` on.

Usually the agent's own words: its title and reason, with a redacted look at
the call it wants run. Not when the call answers somebody outside the pod.
When a member's agent drafts an answer to a question a stranger asked, the
member approves it before anything goes back (``NotificationEntity.respond``),
and what they approve is the card -- so that card is the server's: it names the
group the answer goes to, shows every word of it, and offers no "approve for
the session". Nothing on it comes from the model's choice of title or reason,
which a run that read the stranger's question could have been steered into.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.conversations_for_surfaces import (
    OUTSIDE_ANSWER_TOOL,
    PendingInteraction,
)
from app.modules.agent_surfaces.domain.models import SurfaceApprovalRenderPlan
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.services.approval_preview import (
    approval_action_summary,
    redact_card_text,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_approval_render_plan,
)


@dataclass(frozen=True, slots=True)
class OutsideAnswerCard:
    title: str
    reason: str


async def approval_plan(
    uow: SqlAlchemyUnitOfWork,
    pending: PendingInteraction,
    conversation_id: UUID,
    tool_call_id: str | None,
) -> SurfaceApprovalRenderPlan:
    """The approval card for a paused ``request_approval`` call."""
    callback_id = pending.tool_call_id or str(tool_call_id or "")
    outside = await outside_answer_card(uow, pending)
    if outside is not None:
        return build_approval_render_plan(
            conversation_id=conversation_id,
            tool_call_id=callback_id,
            title=outside.title,
            reason=outside.reason,
            tool_name=None,
            allow_session=False,
        )
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


async def outside_answer_card(
    uow: SqlAlchemyUnitOfWork, pending: PendingInteraction
) -> OutsideAnswerCard | None:
    """The card for this pause if it approves an answer to a stranger, else None."""
    tool_args = pending.tool_args or {}
    if str(tool_args.get("tool_name") or "") != OUTSIDE_ANSWER_TOOL:
        return None
    args = tool_args.get("args")
    args = args if isinstance(args, dict) else {}
    try:
        notification_id = UUID(str(args.get("notification_id") or ""))
    except ValueError:
        return None
    notification = await NotificationRepository(uow).get(notification_id)
    if notification is None or not notification.from_outside:
        return None
    return outside_answer_card_for(
        group_title=notification.origin_group_title,
        summary=str(args.get("summary") or ""),
    )


def outside_answer_card_for(
    *, group_title: str | None, summary: str
) -> OutsideAnswerCard:
    where = f"“{group_title}”" if group_title else "the group"
    words = summary.strip() or "(nothing)"
    return OutsideAnswerCard(
        title=f"Send this answer to {where}?",
        reason=(
            f"Someone outside the pod asked this in {where}. If you approve, "
            f"only these exact words go back to them:\n\n{words}"
        ),
    )
