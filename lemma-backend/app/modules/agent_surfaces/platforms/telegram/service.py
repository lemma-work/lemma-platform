"""Telegram Bot API operations (messaging, files, chat metadata)."""

from __future__ import annotations

import mimetypes
from html import escape
from pathlib import Path
from typing import Any

import httpx
from pydantic_ai.tools import RunContext

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TelegramSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy, with_retry
from app.modules.agent_surfaces.platforms.rendering import chunk_text, to_markdown_v2
from app.modules.agent_surfaces.platforms.telegram.client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramApiError,
    TelegramClient,
    classify_telegram_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.telegram.models import (
    TelegramCurrentChatParams,
    TelegramCurrentChatResult,
    TelegramFileAttachment,
)
from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

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
        except Exception:
            cached = None
        if cached:
            return cached
        try:
            result = await self._client.call("getMe", {})
            info = (result.get("result") or {}) if isinstance(result, dict) else {}
            if info:
                try:
                    await cache.set_json(token, info)
                except Exception:
                    pass
                return info
        except Exception as exc:
            logger.debug("getMe failed while resolving bot info: %s", exc)
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
        reply_to = event.reply_target.get("message_id")
        reply_markup = (metadata or {}).get("reply_markup")

        raw_chunks = chunk_text(message, limit=TELEGRAM_MESSAGE_LIMIT) or [message or ""]
        for index, raw_chunk in enumerate(raw_chunks):
            payload: dict[str, Any] = {"chat_id": chat_id}
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            # Thread the reply / attach a keyboard only on the first chunk.
            if index == 0 and reply_to:
                payload["reply_parameters"] = {
                    "message_id": reply_to,
                    "allow_sending_without_reply": True,
                }
            if index == 0 and isinstance(reply_markup, dict):
                payload["reply_markup"] = reply_markup
            await self._send_chunk(payload, raw_chunk)

    async def _send_chunk(self, payload: dict[str, Any], raw_text: str) -> None:
        rendered = to_markdown_v2(raw_text)
        use_markdown = len(rendered) <= TELEGRAM_MESSAGE_LIMIT
        body = {**payload, "text": rendered if use_markdown else raw_text}
        if use_markdown:
            body["parse_mode"] = "MarkdownV2"
        try:
            await self._call_with_retry("sendMessage", body)
        except TelegramApiError as exc:
            if not (use_markdown and exc.is_parse_entities_error):
                logger.warning(
                    "Telegram sendMessage failed chat=%s: %s",
                    payload.get("chat_id"),
                    exc.description,
                )
                raise
            logger.warning(
                "Telegram MarkdownV2 parse failed chat=%s, retrying as plain text: %s",
                payload.get("chat_id"),
                exc.description,
            )
            plain_body = {k: v for k, v in payload.items()}
            plain_body["text"] = raw_text
            await self._call_with_retry("sendMessage", plain_body)

    async def _call_with_retry(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        except (TypeError, ValueError):
            return None

    async def send_display_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        reply_to = event.reply_target.get("message_id")

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _telegram_display_resource_text(render_plan),
            "parse_mode": "HTML",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
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
            pass

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send/edit a live progress message; return {"message_id": ...}."""
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        if not self._bot_token or not chat_id:
            return progress_handle
        text = f"⏳ {progress_text}"
        message_id = (progress_handle or {}).get("message_id")
        try:
            if message_id:
                await self._call_with_retry(
                    "editMessageText",
                    {"chat_id": chat_id, "message_id": message_id, "text": text},
                )
                return progress_handle
            payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
            thread_id = self._message_thread_id(event)
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            result = await self._call_with_retry("sendMessage", payload)
            new_id = ((result or {}).get("result") or {}).get("message_id")
            return {"message_id": new_id} if new_id else progress_handle
        except TelegramApiError as exc:
            if exc.is_not_modified:
                return progress_handle
            raise

    async def end_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Delete the streaming progress message (the final answer is sent
        separately as its own message)."""
        chat_id = event.reply_target.get("chat_id") or event.external_channel_id
        message_id = (progress_handle or {}).get("message_id")
        if not chat_id or not message_id:
            return
        try:
            await self._client.call(
                "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
            )
        except Exception:
            pass

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
        file_id = str(
            attachment.get("file_id") or attachment.get("id") or ""
        ).strip()
        if not self._bot_token or not file_id:
            return None
        async with httpx.AsyncClient(timeout=60.0) as client:
            metadata_response = await client.post(
                f"{self._client.base_url}/getFile",
                json={"file_id": file_id},
            )
            metadata_response.raise_for_status()
            file_path = str(
                ((metadata_response.json() or {}).get("result") or {}).get(
                    "file_path"
                )
                or ""
            ).strip()
            if not file_path:
                return None
            download_url = f"{self._client.file_base_url}/{file_path.lstrip('/')}"
            file_response = await client.get(download_url)
            file_response.raise_for_status()
            content = file_response.content
        file_name = (
            str(attachment.get("name") or "").strip()
            or Path(file_path).name
            or "telegram_file"
        )
        mime_type = (
            str(attachment.get("mime_type") or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
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
    action = render_plan.primary_action
    if action is not None:
        parts.append(
            f'<a href="{escape(action.url, quote=True)}">{escape(action.label)}</a>'
        )
    return "\n\n".join(parts)


def _truncate_telegram_button_text(value: str) -> str:
    text = " ".join(str(value or "").split()) or "Open"
    return text if len(text) <= 64 else text[:63].rstrip() + "..."
