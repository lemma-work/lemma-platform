"""Questions and decisions as Telegram inline keyboards.

Each button's ``callback_data`` is a short token from the callback store,
because Telegram caps callback_data at 64 bytes and a callback id alone can be
longer. Sending goes through the service's own ``send_message``, passed in, so
the keyboard rides on a message rendered and chunked the ordinary way.
"""

from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    TelegramReplyTarget,
    reply_target_as,
)

from collections.abc import Awaitable, Callable

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.decision_card import DecisionCardHandle
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.envelope import PartDelivery
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceQuestionRenderPlan,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.decision_cards import remember_decision_card
from app.modules.agent_surfaces.platforms.telegram import outbound
from app.modules.agent_surfaces.platforms.telegram.callback_token_store import (
    CALLBACK_STORE_ERRORS,
    put_callback_token,
)

logger = get_logger(__name__)

# Sentinel callback value meaning "the user wants to type a free-text answer";
# the tap resolves to no answer so their next typed message resumes the run.
OTHER_CALLBACK_VALUE = "__lemma_other__"

#: ``TelegramPlatformService.send_message``: the id of the first message sent.
type SendMessage = Callable[..., Awaitable[str | None]]


async def render_choices(
    send_message: SendMessage,
    event: ParsedInboundSurfaceEvent,
    question_plan: SurfaceQuestionRenderPlan,
) -> bool | PartDelivery:
    """Render ask_user questions as native inline keyboards.

    One message per question; each option is a button whose ``callback_data``
    is a short token resolving to ``{callback_id, header, value}`` (Telegram
    caps callback_data at 64 bytes). A trailing "Other" button lets the user
    type a free-text answer instead. Returns ``False`` (caller falls back to
    formatted text) when there is no chat target or any question is
    multi-select (not expressible as a single-tap keyboard).
    """
    chat_id = (
        reply_target_as(event, TelegramReplyTarget).chat_id or event.external_channel_id
    )
    if not chat_id:
        return False
    if any(q.multi_select for q in question_plan.questions):
        return False
    for index, question in enumerate(question_plan.questions):
        rows: list[list[dict[str, str]]] = []
        for option in question.options:
            token = await put_callback_token(
                {
                    "callback_id": question_plan.callback_id,
                    "header": question.header,
                    "value": option.label,
                }
            )
            text = f"⭐ {option.label}" if option.recommended else option.label
            rows.append([{"text": text[:64], "callback_data": token}])
        other_token = await put_callback_token(
            {
                "callback_id": question_plan.callback_id,
                "header": question.header,
                "value": OTHER_CALLBACK_VALUE,
            }
        )
        rows.append([{"text": "✏️ Other (type a reply)", "callback_data": other_token}])
        try:
            message_id = await send_message(
                event,
                outbound.question_with_option_notes(question),
                metadata={"reply_markup": {"inline_keyboard": rows}},
            )
        except platform_transport_errors():
            if not index:
                # Nothing went out: the caller's fallback sends every
                # question as text, and none of them is a duplicate.
                raise
            # The earlier questions are already in the chat with buttons.
            # Raising made the caller resend all of them as text, so the
            # first question appeared twice. Ask only what is left.
            logger.warning(
                "agent_surfaces.telegram.questions_partially_delivered.degraded",
                sent_questions=index,
                total_questions=len(question_plan.questions),
                exc_info=True,
            )
            await send_message(
                event,
                question_plan.model_copy(
                    update={"questions": question_plan.questions[index:]}
                ).to_plain_text(),
            )
            # Delivered, but not all of it as controls: reporting True says
            # every question is tappable, and nothing then accepts a typed
            # answer to the ones that arrived as words.
            return PartDelivery.DEGRADED
        await _remember_choice_card(question_plan.callback_id, chat_id, message_id)
    return True


async def _remember_choice_card(
    callback_id: str, chat_id: object, message_id: str | None
) -> None:
    """Remember a keyboard so it can be taken away once its pause is over."""
    if message_id is not None:
        await remember_decision_card(
            callback_id,
            DecisionCardHandle(
                platform="TELEGRAM", channel=str(chat_id), message_id=message_id
            ),
        )


async def render_decision(
    send_message: SendMessage,
    event: ParsedInboundSurfaceEvent,
    approval_plan: SurfaceApprovalRenderPlan,
) -> bool:
    """Render a request_approval prompt as an inline keyboard.

    One message with Approve/Deny (and optionally Approve-for-session)
    buttons; each button's ``callback_data`` is a short token resolving to
    ``{callback_id, decision}`` (Telegram caps callback_data at 64 bytes).
    """
    chat_id = (
        reply_target_as(event, TelegramReplyTarget).chat_id or event.external_channel_id
    )
    if not chat_id:
        return False
    rows: list[list[dict[str, str]]] = []
    try:
        for button in approval_plan.buttons:
            token = await put_callback_token(
                {
                    "callback_id": approval_plan.callback_id,
                    "decision": button.decision,
                }
            )
            rows.append([{"text": button.label[:64], "callback_data": token}])
    except CALLBACK_STORE_ERRORS:
        # Buttons nothing could resolve would read as expired on the first
        # tap. Asking in words instead is the fallback a person can answer.
        logger.warning(
            "agent_surfaces.telegram.decision_buttons_unavailable.degraded",
            exc_info=True,
        )
        return False
    body_lines = [approval_plan.title]
    if approval_plan.reason:
        body_lines.append(approval_plan.reason)
    if approval_plan.action_summary:
        body_lines.append(f"Action: {approval_plan.action_summary}")
    message_id = await send_message(
        event,
        "\n\n".join(body_lines),
        metadata={"reply_markup": {"inline_keyboard": rows}},
    )
    await _remember_choice_card(approval_plan.callback_id, chat_id, message_id)
    return True
