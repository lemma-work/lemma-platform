from __future__ import annotations

from app.modules.agent_surfaces.domain.reply_targets import (
    TelegramReplyTarget,
    reply_target_as,
)

from app.modules.agent_surfaces.domain.models import ProgressHandle, ProgressHandleKind

from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.decision_card import DecisionCardHandle
from app.modules.agent_surfaces.platforms.delivery import note_partial_delivery
from app.modules.agent_surfaces.platforms.rendering import (
    chunk_text,
    to_markdown_v2,
    utf16_length,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramApiError,
    TelegramClient,
)

logger = get_logger(__name__)

TelegramCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


#: How many times a refused-as-too-long chunk is halved before giving up. Each
#: halving at least halves the text, so a chunk that is still too long after
#: this many is not being refused for its length.
_MAX_RESPLITS = 3

#: What only the first part of a message carries: the reply it threads under
#: and its buttons belong to the message once, not to every part.
_FIRST_PART_ONLY = ("reply_parameters", "reply_markup")


async def send_chunk(
    call_with_retry: TelegramCall,
    payload: dict[str, Any],
    raw_text: str,
    *,
    resplits_left: int = _MAX_RESPLITS,
) -> dict[str, object]:
    """Send one chunk; the Bot API's answer, which names the message it made.

    Split smaller if Telegram says it is too long: the chunker counts UTF-16
    units, but rendering adds escapes and a rich message measures its own way.
    A "message is too long" refusal is permanent for those exact bytes and
    nothing else, so the words go out in halves rather than not at all. The
    answer returned is the first part's, the one a keyboard rides on.
    """
    try:
        return await _send_one(call_with_retry, payload, raw_text)
    except TelegramApiError as exc:
        if not exc.is_message_too_long or resplits_left <= 0:
            raise
        halves = chunk_text(
            raw_text, limit=max(utf16_length(raw_text) // 2, 1), length=utf16_length
        )
        rest = {k: v for k, v in payload.items() if k not in _FIRST_PART_ONLY}
        first: dict[str, object] = {}
        sent = 0
        try:
            for half in halves:
                answer = await send_chunk(
                    call_with_retry,
                    payload if sent == 0 else rest,
                    half,
                    resplits_left=resplits_left - 1,
                )
                if sent == 0:
                    first = answer
                sent += 1
        finally:
            if 0 < sent < len(halves):
                note_partial_delivery()
        return first


async def _send_one(
    call_with_retry: TelegramCall,
    payload: dict[str, Any],
    raw_text: str,
) -> dict[str, object]:
    try:
        return await call_with_retry(
            "sendRichMessage",
            {**payload, "rich_message": {"markdown": raw_text}},
        )
    except TelegramApiError as exc:
        if not can_fallback_from_rich_message(exc):
            raise
    rendered = to_markdown_v2(raw_text)
    use_markdown = utf16_length(rendered) <= TELEGRAM_MESSAGE_LIMIT
    body = {**payload, "text": rendered if use_markdown else raw_text}
    if use_markdown:
        body["parse_mode"] = "MarkdownV2"
    try:
        return await call_with_retry("sendMessage", body)
    except TelegramApiError as exc:
        if not (use_markdown and exc.is_parse_entities_error):
            raise
        return await call_with_retry("sendMessage", {**payload, "text": raw_text})


def first_message_id(sent: list[dict[str, object]]) -> str | None:
    """The message the first chunk became -- the one a keyboard rides on."""
    result = sent[0].get("result") if sent else None
    message_id = result.get("message_id") if isinstance(result, dict) else None
    return str(message_id) if message_id is not None else None


async def retire_decision_card(
    client: TelegramClient, handle: DecisionCardHandle
) -> None:
    """Take a superseded card's buttons away. Its words stay, as written."""
    await client.call(
        "editMessageReplyMarkup",
        {
            "chat_id": handle.channel,
            "message_id": int(handle.message_id),
            "reply_markup": {"inline_keyboard": []},
        },
    )


async def acknowledge_interaction(
    client: TelegramClient,
    interaction: ParsedSurfaceInteraction,
    *,
    text: str | None,
    show_alert: bool,
    clear_actions: bool,
) -> None:
    callback_query = (interaction.raw_payload or {}).get("callback_query") or {}
    callback_query_id = str(callback_query.get("id") or "").strip()
    if callback_query_id:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        if show_alert:
            payload["show_alert"] = True
        try:
            await client.call("answerCallbackQuery", payload)
        except TelegramApiError:
            logger.debug("agent_surfaces.telegram.callback_acknowledgement_best_effort")
    if not clear_actions:
        return
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return
    try:
        await client.call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except TelegramApiError:
        logger.debug("agent_surfaces.telegram.callback_keyboard_cleanup_best_effort")


async def stream_progress(
    call_with_retry: TelegramCall,
    event: ParsedInboundSurfaceEvent,
    progress_text: str,
    progress_handle: ProgressHandle | None,
    *,
    bot_token: str,
) -> ProgressHandle | None:
    chat_id = (
        reply_target_as(event, TelegramReplyTarget).chat_id or event.external_channel_id
    )
    if not bot_token or not chat_id:
        return progress_handle
    draft = await _send_rich_progress_draft(
        call_with_retry,
        event,
        str(chat_id),
        progress_text,
        progress_handle,
    )
    if draft is not None:
        return draft
    return await _stream_plain_progress(
        call_with_retry,
        event,
        str(chat_id),
        progress_text,
        progress_handle,
    )


async def _send_rich_progress_draft(
    call_with_retry: TelegramCall,
    event: ParsedInboundSurfaceEvent,
    chat_id: str,
    progress_text: str,
    progress_handle: ProgressHandle | None,
) -> ProgressHandle | None:
    if not event.is_dm or (
        progress_handle is not None
        and progress_handle.kind is ProgressHandleKind.MESSAGE
    ):
        return None
    try:
        draft_id = int(
            progress_handle.message_id
            if progress_handle is not None
            else reply_target_as(event, TelegramReplyTarget).message_id or 1
        )
    except TypeError, ValueError:
        draft_id = 1
    try:
        await call_with_retry(
            "sendRichMessageDraft",
            {
                "chat_id": int(chat_id),
                "draft_id": int(draft_id) or 1,
                "rich_message": {
                    "html": f"<tg-thinking>{escape(progress_text)}</tg-thinking>"
                },
            },
        )
        return ProgressHandle(
            kind=ProgressHandleKind.DRAFT, message_id=str(int(draft_id) or 1)
        )
    except TelegramApiError as exc:
        if not can_fallback_from_rich_message(exc):
            raise
        return None


async def _stream_plain_progress(
    call_with_retry: TelegramCall,
    event: ParsedInboundSurfaceEvent,
    chat_id: str,
    progress_text: str,
    progress_handle: ProgressHandle | None,
) -> ProgressHandle | None:
    message_id = (
        progress_handle.message_id
        if progress_handle is not None
        and progress_handle.kind is ProgressHandleKind.MESSAGE
        else None
    )
    try:
        if message_id:
            await call_with_retry(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "text": f"⏳ {progress_text}",
                },
            )
            return progress_handle
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": f"⏳ {progress_text}",
        }
        thread_id = _message_thread_id(event)
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        result = await call_with_retry("sendMessage", payload)
        new_id = ((result or {}).get("result") or {}).get("message_id")
        return (
            ProgressHandle(kind=ProgressHandleKind.MESSAGE, message_id=str(new_id))
            if new_id
            else progress_handle
        )
    except TelegramApiError as exc:
        if exc.is_not_modified:
            return progress_handle
        raise


async def end_progress(
    client: TelegramClient,
    event: ParsedInboundSurfaceEvent,
    progress_handle: ProgressHandle | None,
) -> None:
    chat_id = (
        reply_target_as(event, TelegramReplyTarget).chat_id or event.external_channel_id
    )
    # A draft is replaced by the answer itself, so there is nothing to delete.
    if (
        progress_handle is None
        or progress_handle.kind is not ProgressHandleKind.MESSAGE
        or not chat_id
    ):
        return
    try:
        await client.call(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": int(progress_handle.message_id)},
        )
    except TelegramApiError:
        logger.debug(
            "agent_surfaces.service.telegram_progress_message_cleanup_best.observed",
            chat_id=chat_id,
        )


def can_fallback_from_rich_message(exc: TelegramApiError) -> bool:
    description = str(exc.description or "").lower()
    return exc.status_code in {400, 404} and (
        "not found" in description
        or "unknown method" in description
        or "rich" in description
        or "parse" in description
        or "unsupported" in description
    )


def reply_parameters(event: ParsedInboundSurfaceEvent) -> dict[str, Any] | None:
    """What an outbound should quote, or ``None`` to quote nothing.

    Groups and forum topics only, never a one-to-one DM. The event an outbound
    is built from is the conversation link's *last* inbound, not the message
    this run is answering -- so in a DM the quote pointed at whatever the person
    happened to say most recently, which is how every reply ended up tagging an
    unrelated message. In a DM it disambiguates nothing anyway: two of us, one
    thread. Nor when the run answers a message typed in Lemma, which nobody in
    the group saw (``answers_inbound``).

    The id is coerced to an int because that is what the Bot API takes, and
    because a non-numeric one -- a debounced burst's synthetic ``batch:5-6``,
    say -- would fail the entire send rather than just the quoting.
    """
    if event.is_dm or not event.answers_inbound:
        return None
    try:
        message_id = int(reply_target_as(event, TelegramReplyTarget).message_id or 0)
    except TypeError, ValueError:
        return None
    if message_id <= 0:
        return None
    return {"message_id": message_id, "allow_sending_without_reply": True}


def _message_thread_id(event: ParsedInboundSurfaceEvent) -> int | None:
    raw = reply_target_as(event, TelegramReplyTarget).message_thread_id
    if raw in (None, "", "0", 0):
        raw = event.metadata.get("message_thread_id")
    if raw in (None, "", "0", 0):
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None
