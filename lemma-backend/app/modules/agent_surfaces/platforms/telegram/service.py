"""Telegram Bot API operations (messaging, files, chat metadata)."""

from __future__ import annotations

from html import escape
from typing import Any

import httpx
from redis.exceptions import RedisError
from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.telegram.attachment_naming import (
    resolve_attachment_name_and_mime,
)
from app.modules.agent_surfaces.platforms.telegram.callback_token_store import (
    put_callback_token,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TelegramSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy, with_retry
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.common import assert_safe_api_base
from app.modules.agent_surfaces.platforms.telegram.client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.telegram.message_experience import (
    acknowledge_interaction as acknowledge_telegram_interaction,
    end_progress as end_telegram_progress,
    reply_parameters as telegram_reply_parameters,
    send_chunk,
    stream_progress as stream_telegram_progress,
)
from app.modules.agent_surfaces.platforms.telegram.models import (
    TelegramCurrentChatParams,
    TelegramCurrentChatResult,
    TelegramFileAttachment,
)
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)

logger = get_logger(__name__)

# Sentinel callback value meaning "the user wants to type a free-text answer";
# the tap resolves to no answer so their next typed message resumes the run.
_OTHER_CALLBACK_VALUE = "__lemma_other__"

# Shared Redis cache: bot_token → getMe result dict (username + id), so mention
# verification doesn't hit the Telegram API on every message and is shared across
# replicas. Redis unavailable -> refetch (never fails).
_bot_info_cache: RedisJsonCache | None = None


def _get_bot_info_cache() -> RedisJsonCache:
    global _bot_info_cache
    if _bot_info_cache is None or _bot_info_cache._redis_url != settings.redis_url:
        _bot_info_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:telegram-bot",
            ttl_seconds=3600,
        )
    return _bot_info_cache


class TelegramPlatformService:
    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials
        self._client = TelegramClient.from_credentials(credentials)
        self._bot_token = self._client._bot_token
        self._retry_policy = RetryPolicy()

    async def _get_bot_info(self) -> dict[str, Any] | None:
        """Return this bot's getMe result, cached per process by token."""
        token = self._bot_token
        if not token:
            return None
        cache = _get_bot_info_cache()
        try:
            cached = await cache.get_json(token)
        except RedisError, OSError, TimeoutError, ValueError:
            cached = None
        if cached:
            return cached
        try:
            result = await self._client.call("getMe", {})
            info = (result.get("result") or {}) if isinstance(result, dict) else {}
            if info:
                try:
                    await cache.set_json(token, info)
                except RedisError, OSError, TimeoutError, TypeError:
                    pass
                return info
        except Exception:
            logger.debug(
                "agent_surfaces.service.getme_while_resolving_bot_info.observed"
            )
        return None

    async def get_bot_username(self) -> str | None:
        """Return this bot's @username (without the leading @)."""
        info = await self._get_bot_info()
        return str((info or {}).get("username") or "").strip() or None

    async def get_bot_user_id(self) -> str | None:
        """Return this bot's numeric user id (for text_mention verification)."""
        info = await self._get_bot_info()
        bot_id = (info or {}).get("id")
        return str(bot_id).strip() if bot_id is not None else None

    async def fetch_sender_profile(
        self, event: ParsedInboundSurfaceEvent
    ) -> SurfaceSenderProfile | None:
        return SurfaceSenderProfile(
            display_name=event.sender_display_name,
            external_user_id=event.sender_external_user_id,
            phone=event.sender_phone,
            raw_profile={
                "sender_username": event.metadata.get("sender_username"),
                "chat_id": event.metadata.get("chat_id"),
                "contact_shared": event.metadata.get("contact_shared"),
            },
        )

    async def send_message(
        self,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send an assistant reply, rendered safely as MarkdownV2.

        The message is rendered to MarkdownV2 and split under Telegram's 4096
        character limit; each chunk is sent with bounded retry on transient
        failures. If MarkdownV2 fails to parse (``can't parse entities``) the
        chunk is retried once as plain text so a formatting edge case never
        drops the user's reply.
        """
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        thread_id = self._message_thread_id(event)
        reply_parameters = telegram_reply_parameters(event)
        reply_markup = (metadata or {}).get("reply_markup")
        retry_action = (metadata or {}).get("retry_action") is True
        if retry_action and not isinstance(reply_markup, dict):
            retry_token = await put_callback_token({"action": "retry"})
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "Try again", "callback_data": retry_token}]
                ]
            }

        # `chunk_text("")` is `[]`, and the `or [message or ""]` this replaces
        # made that one empty chunk: a blank bubble, and for an approval one
        # with no words and no buttons.
        raw_chunks = chunk_text(message, limit=TELEGRAM_MESSAGE_LIMIT)
        if not raw_chunks:
            logger.warning(
                "agent_surfaces.telegram.empty_message_not_sent",
                has_reply_markup=isinstance(reply_markup, dict),
            )
            return
        for index, raw_chunk in enumerate(raw_chunks):
            payload: dict[str, Any] = {"chat_id": chat_id}
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            # Thread the reply / attach a keyboard only on the first chunk.
            if index == 0 and reply_parameters is not None:
                payload["reply_parameters"] = reply_parameters
            if index == 0 and isinstance(reply_markup, dict):
                payload["reply_markup"] = reply_markup
            await self._send_chunk(payload, raw_chunk)

    async def _render_choices(
        self,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render ask_user questions as native inline keyboards.

        One message per question; each option is a button whose ``callback_data``
        is a short token resolving to ``{callback_id, header, value}`` (Telegram
        caps callback_data at 64 bytes). A trailing "Other" button lets the user
        type a free-text answer instead. Returns ``False`` (caller falls back to
        formatted text) when there is no chat target or any question is
        multi-select (not expressible as a single-tap keyboard).
        """
        del metadata
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        if not chat_id:
            return False
        if any(q.multi_select for q in question_plan.questions):
            return False
        for question in question_plan.questions:
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
                    "value": _OTHER_CALLBACK_VALUE,
                }
            )
            rows.append(
                [{"text": "✏️ Other (type a reply)", "callback_data": other_token}]
            )
            await self.send_message(
                event,
                question.question,
                metadata={"reply_markup": {"inline_keyboard": rows}},
            )
        return True

    async def _render_decision(
        self,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as an inline keyboard.

        One message with Approve/Deny (and optionally Approve-for-session)
        buttons; each button's ``callback_data`` is a short token resolving to
        ``{callback_id, decision}`` (Telegram caps callback_data at 64 bytes).
        """
        del metadata
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        if not chat_id:
            return False
        rows: list[list[dict[str, str]]] = []
        for button in approval_plan.buttons:
            token = await put_callback_token(
                {
                    "callback_id": approval_plan.callback_id,
                    "decision": button.decision,
                }
            )
            rows.append([{"text": button.label[:64], "callback_data": token}])
        body_lines = [approval_plan.title]
        if approval_plan.reason:
            body_lines.append(approval_plan.reason)
        if approval_plan.action_summary:
            body_lines.append(f"Action: {approval_plan.action_summary}")
        await self.send_message(
            event,
            "\n\n".join(body_lines),
            metadata={"reply_markup": {"inline_keyboard": rows}},
        )
        return True

    async def _send_chunk(self, payload: dict[str, Any], raw_text: str) -> None:
        await send_chunk(self._call_with_retry, payload, raw_text)

    async def acknowledge_interaction(
        self,
        interaction: ParsedSurfaceInteraction,
        *,
        text: str | None = None,
        show_alert: bool = False,
        clear_actions: bool = False,
    ) -> None:
        await acknowledge_telegram_interaction(
            self._client,
            interaction,
            text=text,
            show_alert=show_alert,
            clear_actions=clear_actions,
        )

    async def _call_with_retry(
        self, method: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await with_retry(
            lambda: self._client.call(method, payload),
            policy=self._retry_policy,
            classify=classify_telegram_error,
            retry_after=telegram_retry_after,
        )

    @staticmethod
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

    async def _render_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        reply_parameters = telegram_reply_parameters(event)

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _telegram_display_resource_text(render_plan),
            "parse_mode": "HTML",
        }
        if reply_parameters is not None:
            payload["reply_parameters"] = reply_parameters
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        action = render_plan.primary_action
        if action is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": _truncate_telegram_button_text(action.label),
                            "url": action.url,
                        }
                    ]
                ]
            }

        await self._call_with_retry("sendMessage", payload)

    async def send_file_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Send raw file bytes to the inbound chat (egress, no RunContext).

        Returns True on success; False when the chat/credentials are missing so
        the caller can fall back to delivering a URL link.
        """
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        if not self._bot_token or not chat_id:
            return False
        send_type = _resolve_telegram_send_type(
            delivery_mode="auto", mime_type=mime_type
        )
        method_name, file_field = _telegram_method_for_send_type(send_type)
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._client.base_url}/{method_name}",
                data=data,
                files={file_field: (file_name, file_bytes, mime_type)},
            )
            response.raise_for_status()
        return True

    async def send_voice_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        audio_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Send audio as a native Telegram voice note (sendVoice; OGG/Opus).

        Returns True on success; False when the chat/credentials are missing so
        the caller can fall back to a normal file attachment.
        """
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        if not self._bot_token or not chat_id:
            return False
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._client.base_url}/sendVoice",
                data=data,
                files={"voice": (file_name, audio_bytes, mime_type or "audio/ogg")},
            )
            response.raise_for_status()
        return True

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        payload: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        # Best-effort: a failed typing indicator must never break the run.
        try:
            await self._client.call("sendChatAction", payload)
        except Exception:
            logger.debug(
                "agent_surfaces.service.telegram_typing_indicator_best_effort.observed"
            )

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await stream_telegram_progress(
            self._call_with_retry,
            event,
            progress_text,
            progress_handle,
            bot_token=self._bot_token,
        )

    async def end_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        await end_telegram_progress(self._client, event, progress_handle)

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Telegram attachment (no RunContext).

        Used by inbound auto-ingest; mirrors the getFile + file-download flow of
        the former ``download_file`` tool but takes a raw attachment dict.
        """
        del event
        file_id = str(attachment.get("file_id") or attachment.get("id") or "").strip()
        if not self._bot_token or not file_id:
            return None
        async with httpx.AsyncClient(timeout=60.0) as client:
            metadata_response = await client.post(
                f"{self._client.base_url}/getFile",
                json={"file_id": file_id},
            )
            metadata_response.raise_for_status()
            file_path = str(
                ((metadata_response.json() or {}).get("result") or {}).get("file_path")
                or ""
            ).strip()
            if not file_path:
                return None
            download_url = f"{self._client.file_base_url}/{file_path.lstrip('/')}"
            # The file base is derived from the same tenant-supplied
            # `api_base_url` as the API base, by a different function — so it
            # needs its own check rather than inheriting the one in `call`.
            await assert_safe_api_base(download_url, platform="Telegram")
            async with client.stream("GET", download_url) as file_response:
                file_response.raise_for_status()
                content = await read_capped(
                    file_response.aiter_bytes(),
                    max_bytes=INBOUND_ATTACHMENT_BYTE_CAP,
                )
        # Naming is its own module because getting it wrong is silent: a photo
        # arrives with no filename and no mime type, and every layer downstream
        # types a file by its name. See `attachment_naming`.
        file_name, mime_type = resolve_attachment_name_and_mime(
            attachment=attachment,
            file_path=file_path,
            content=content,
        )
        return content, file_name, mime_type

    async def get_current_chat(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: TelegramCurrentChatParams,
    ) -> TelegramCurrentChatResult:
        del request
        metadata = self._telegram_metadata(ctx)
        attachment_names = [
            attachment.name
            for attachment in self._current_message_attachments(ctx)
            if attachment.name
        ]
        return TelegramCurrentChatResult(
            success=True,
            message="Resolved current Telegram chat details.",
            chat_id=ctx.deps.external_channel_id,
            chat_type=metadata.chat_type if metadata is not None else None,
            message_thread_id=metadata.message_thread_id
            if metadata is not None
            else None,
            is_topic_message=metadata.is_topic_message
            if metadata is not None
            else False,
            attachment_names=attachment_names,
        )

    def _telegram_metadata(
        self,
        ctx: RunContext[ConversationContext],
    ) -> TelegramSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, TelegramSurfaceEventMetadata):
            return metadata
        return None

    def _current_message_attachments(
        self,
        ctx: RunContext[ConversationContext],
    ) -> list[TelegramFileAttachment]:
        metadata = self._telegram_metadata(ctx)
        if metadata is None:
            return []
        return common.coerce_attachments(metadata.attachments, TelegramFileAttachment)


def _resolve_telegram_send_type(*, delivery_mode: str, mime_type: str) -> str:
    requested = str(delivery_mode or "auto").lower()
    if requested != "auto":
        return requested
    if mime_type.startswith("image/"):
        return "photo"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


def _telegram_method_for_send_type(send_type: str) -> tuple[str, str]:
    normalized = str(send_type).lower()
    if normalized == "photo":
        return "sendPhoto", "photo"
    if normalized == "audio":
        return "sendAudio", "audio"
    if normalized == "video":
        return "sendVideo", "video"
    return "sendDocument", "document"


def _telegram_display_resource_text(render_plan: SurfaceDisplayRenderPlan) -> str:
    parts = [f"<b>{escape(render_plan.title)}</b>"]
    if render_plan.summary:
        parts.append(escape(render_plan.summary))
    for line in render_plan.detail_lines[:5]:
        parts.append(f"<blockquote>{escape(line)}</blockquote>")
    if render_plan.preview_block:
        parts.append(f"<pre>{escape(render_plan.preview_block)}</pre>")
    action = render_plan.primary_action
    if action is not None:
        parts.append(
            f'<a href="{escape(action.url, quote=True)}">{escape(action.label)}</a>'
        )
    return "\n\n".join(parts)


def _truncate_telegram_button_text(value: str) -> str:
    text = " ".join(str(value or "").split()) or "Open"
    return text if len(text) <= 64 else text[:63].rstrip() + "..."
