from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape
from typing import Any

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.platforms.rendering import to_markdown_v2
from app.modules.agent_surfaces.platforms.telegram.client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramApiError,
    TelegramClient,
)

logger = get_logger(__name__)

TelegramCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def send_chunk(
    call_with_retry: TelegramCall,
    payload: dict[str, Any],
    raw_text: str,
) -> None:
    try:
        await call_with_retry(
            "sendRichMessage",
            {**payload, "rich_message": {"markdown": raw_text}},
        )
        return
    except TelegramApiError as exc:
        if not can_fallback_from_rich_message(exc):
            raise
    rendered = to_markdown_v2(raw_text)
    use_markdown = len(rendered) <= TELEGRAM_MESSAGE_LIMIT
    body = {**payload, "text": rendered if use_markdown else raw_text}
    if use_markdown:
        body["parse_mode"] = "MarkdownV2"
    try:
        await call_with_retry("sendMessage", body)
    except TelegramApiError as exc:
        if not (use_markdown and exc.is_parse_entities_error):
            logger.debug(
                "agent_surfaces.service.telegram_sendmessage_chat_s_s.diagnostic"
            )
            raise
        logger.debug(
            "agent_surfaces.service.telegram_markdownv2_parse_chat_s.diagnostic"
        )
        await call_with_retry("sendMessage", {**payload, "text": raw_text})


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
    progress_handle: dict[str, Any] | None,
    *,
    bot_token: str,
) -> dict[str, Any] | None:
    chat_id = event.reply_target.get("chat_id") or event.external_channel_id
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
    progress_handle: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not event.is_dm or (progress_handle or {}).get("message_id"):
        return None
    draft_id = (progress_handle or {}).get("draft_id")
    if not draft_id:
        try:
            draft_id = int(event.reply_target.get("message_id") or 1)
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
        return {"draft_id": int(draft_id) or 1, "rich_draft": True}
    except TelegramApiError as exc:
        if not can_fallback_from_rich_message(exc):
            raise
        return None


async def _stream_plain_progress(
    call_with_retry: TelegramCall,
    event: ParsedInboundSurfaceEvent,
    chat_id: str,
    progress_text: str,
    progress_handle: dict[str, Any] | None,
) -> dict[str, Any] | None:
    message_id = (progress_handle or {}).get("message_id")
    try:
        if message_id:
            await call_with_retry(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
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
        return {"message_id": new_id} if new_id else progress_handle
    except TelegramApiError as exc:
        if exc.is_not_modified:
            return progress_handle
        raise


async def end_progress(
    client: TelegramClient,
    event: ParsedInboundSurfaceEvent,
    progress_handle: dict[str, Any] | None,
) -> None:
    chat_id = event.reply_target.get("chat_id") or event.external_channel_id
    if (progress_handle or {}).get("rich_draft"):
        return
    message_id = (progress_handle or {}).get("message_id")
    if not chat_id or not message_id:
        return
    try:
        await client.call(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
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


def _message_thread_id(event: ParsedInboundSurfaceEvent) -> int | None:
    raw = event.reply_target.get("message_thread_id")
    if raw in (None, "", "0", 0):
        raw = event.metadata.get("message_thread_id")
    if raw in (None, "", "0", 0):
        return None
    try:
        return int(raw)
    except TypeError, ValueError:
        return None
