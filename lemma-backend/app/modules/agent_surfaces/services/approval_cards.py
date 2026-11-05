"""The card a person approves a paused ``request_approval`` on: the agent's
own title and reason, with a redacted look at the call it wants run."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.approval_wording import (
    grant_note,
    session_scope_sentence,
)
from app.core.authorization.session_approvals import shown_card
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.channels import PendingInteraction
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
    # What an answer lends is what the server wrote on the card when it was
    # shown, so that is what the card names. A card from before that record
    # existed falls back to the model's copy, as it always read.
    shown = await shown_card(session_id=str(conversation_id), approval_id=callback_id)
    permission_ids = (
        shown.get("permission_ids") if shown else tool_args.get("permission_ids")
    )
    named = [str(p) for p in permission_ids] if isinstance(permission_ids, list) else []
    offered = shown.get("grant")
    grant = offered if isinstance(offered, dict) else None
    reason = redact_card_text(str(tool_args.get("reason") or ""))
    if grant is not None:
        # Approving gives the agent the connector for good, once or not: a
        # "for this conversation" option would promise a limit nothing keeps.
        reason = " ".join(
            part
            for part in (reason, grant_note(str(grant.get("connector") or "")))
            if part
        )
    return build_approval_render_plan(
        conversation_id=conversation_id,
        tool_call_id=callback_id,
        title=redact_card_text(
            str(tool_args.get("title") or "Action requires your approval")
        ),
        reason=reason or None,
        tool_name=approval_action_summary(
            str(tool_args.get("tool_name") or ""), tool_args.get("args")
        ),
        # An approve-for-session button only makes sense when the paused call
        # carries a real permission gate; otherwise it is noise.
        allow_session=bool(named) and grant is None,
        session_scope=session_scope_sentence(named),
    )
