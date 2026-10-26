"""Telegram Bot API operations (messaging, files, chat metadata)."""

from __future__ import annotations

from app.modules.agent_surfaces.platforms.outbound_http import outbound_http

from app.modules.agent_surfaces.domain.reply_targets import (
    TelegramReplyTarget,
    reply_target_as,
)

from app.modules.agent_surfaces.domain.models import ProgressHandle

import hashlib
import hmac

from typing import Any

from redis.exceptions import RedisError
from pydantic_ai.tools import RunContext

from app.modules.agent.contracts import ToolContext
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ParsedSurfaceInteraction,
)
from app.modules.agent_surfaces.domain.envelope import PartDelivery
from app.modules.agent_surfaces.domain.models import (
    SurfaceApprovalRenderPlan,
    SurfaceDisplayRenderPlan,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.platforms.telegram import outbound
from app.modules.agent_surfaces.platforms.telegram.attachment_naming import (
    resolve_attachment_name_and_mime,
)
from app.modules.agent_surfaces.platforms.telegram.callback_token_store import (
    retry_keyboard,
)
from app.modules.agent_surfaces.platforms.telegram.choice_cards import (
    render_choices,
    render_decision,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TelegramSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms import common
from app.modules.agent_surfaces.platforms.delivery import (
    RetryPolicy,
    note_partial_delivery,
    with_retry,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text, utf16_length
from app.modules.agent_surfaces.platforms.common import (
    platform_transport_errors,
    assert_safe_api_base,
)
from app.modules.agent_surfaces.platforms.telegram.client import (
    TELEGRAM_MESSAGE_LIMIT,
    TelegramClient,
    classify_telegram_send_error,
    telegram_retry_after,
)
from app.modules.agent_surfaces.platforms.telegram.message_experience import (
    acknowledge_interaction as acknowledge_telegram_interaction,
    end_progress as end_telegram_progress,
    first_message_id,
    reply_parameters as telegram_reply_parameters,
    send_chunk,
    stream_progress as stream_telegram_progress,
)
from app.modules.agent_surfaces.platforms.telegram.models import (
    TelegramFileAttachment,
)
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.domain.credentials import (
    CredentialsLike,
    TelegramCredentials,
)
from app.modules.agent_surfaces.caches import TELEGRAM_BOT_INFO, surface_caches
from app.core.log.log import get_logger
from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)

logger = get_logger(__name__)


def _bot_cache_key(bot_token: str) -> str:
    """A keyed fingerprint of the token, never the token itself.

    A Redis key is visible to SCAN, MONITOR, the slow log and every RDB dump,
    and a bot token in one is a credential anyone with that access can use.
    The token is the HMAC key, so the fingerprint identifies the bot without
    being reversible to it -- the same scheme as the native receivers' leases.
    """
    return hmac.new(
        bot_token.encode("utf-8"), b"telegram:getMe", hashlib.sha256
    ).hexdigest()[:32]


def _get_bot_info_cache() -> RedisJsonCache:
    """Bot → getMe result (username and id), shared across replicas so mention
    checks do not ask Telegram on every message. Redis down -> refetch."""
    return surface_caches.get(TELEGRAM_BOT_INFO)


class TelegramPlatformService:
    def __init__(
        self,
        credentials: CredentialsLike,
        *,
        bot_info_cache: RedisJsonCache | None = None,
    ):
        self.credentials = TelegramCredentials.of(credentials)
        self._bot_info_cache = bot_info_cache
        self._client = TelegramClient.from_credentials(credentials)
        self._bot_token = self._client._bot_token
        self._retry_policy = RetryPolicy()

    async def _get_bot_info(self) -> dict[str, Any] | None:
        """Return this bot's getMe result, cached per process by token."""
        token = self._bot_token
        if not token:
            return None
        cache = self._bot_info_cache or _get_bot_info_cache()
        cache_key = _bot_cache_key(token)
        try:
            cached = await cache.get_json(cache_key)
        except RedisError, OSError, TimeoutError, ValueError:
            cached = None
        if cached:
            return cached
        try:
            result = await self._client.call("getMe", {})
            info = (result.get("result") or {}) if isinstance(result, dict) else {}
            if info:
                try:
                    await cache.set_json(cache_key, info)
                except RedisError, OSError, TimeoutError, TypeError:
                    # Caching `getMe` saves a round-trip, nothing more; `info`
                    # is returned below whether or not the write lands.
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
    ) -> str | None:
        """Send an assistant reply, rendered safely as MarkdownV2.

        The message is rendered to MarkdownV2 and split under Telegram's 4096
        character limit; each chunk is sent with bounded retry on transient
        failures. If MarkdownV2 fails to parse (``can't parse entities``) the
        chunk is retried once as plain text so a formatting edge case never
        drops the user's reply.

        Returns the id of the first message sent -- the one any keyboard rides
        on -- when Telegram named it.
        """
        chat_id = (
            reply_target_as(event, TelegramReplyTarget).chat_id
            or event.external_channel_id
        )
        thread_id = self._message_thread_id(event)
        reply_parameters = telegram_reply_parameters(event)
        reply_markup = (metadata or {}).get("reply_markup")
        retry_action = (metadata or {}).get("retry_action") is True
        if retry_action and not isinstance(reply_markup, dict):
            reply_markup = await retry_keyboard()

        # `chunk_text("")` is `[]`, and the `or [message or ""]` this replaces
        # made that one empty chunk: a blank bubble, and for an approval one
        # with no words and no buttons.
        raw_chunks = chunk_text(
            message, limit=TELEGRAM_MESSAGE_LIMIT, length=utf16_length
        )
        if not raw_chunks:
            logger.warning(
                "agent_surfaces.telegram.empty_message_not_sent",
                has_reply_markup=isinstance(reply_markup, dict),
            )
            return None
        sent: list[dict[str, object]] = []
        for index, raw_chunk in enumerate(raw_chunks):
            payload: dict[str, Any] = {"chat_id": chat_id}
            if thread_id is not None:
                payload["message_thread_id"] = thread_id
            # Thread the reply / attach a keyboard only on the first chunk.
            if index == 0 and reply_parameters is not None:
                payload["reply_parameters"] = reply_parameters
            if index == 0 and isinstance(reply_markup, dict):
                payload["reply_markup"] = reply_markup
            try:
                sent.append(await self._send_chunk(payload, raw_chunk))
            except platform_transport_errors():
                if index:
                    # Earlier chunks are already in the chat. The caller only
                    # sees "not delivered", so say what actually happened.
                    note_partial_delivery()
                    logger.warning(
                        "agent_surfaces.telegram.message_partially_delivered.degraded",
                        sent_chunks=index,
                        total_chunks=len(raw_chunks),
                        exc_info=True,
                    )
                raise
        return first_message_id(sent)

    async def _render_choices(
        self,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool | PartDelivery:
        """Ask the questions as inline keyboards; see ``choice_cards``."""
        del metadata
        return await render_choices(self.send_message, event, question_plan)

    async def _render_decision(
        self,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Ask for the decision as an inline keyboard; see ``choice_cards``."""
        del metadata
        return await render_decision(self.send_message, event, approval_plan)

    async def _send_chunk(
        self, payload: dict[str, Any], raw_text: str
    ) -> dict[str, object]:
        return await send_chunk(self._call_with_retry, payload, raw_text)

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
            classify=classify_telegram_send_error,
            retry_after=telegram_retry_after,
        )

    @staticmethod
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

    async def _render_resource(
        self,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Send a resource as a message; True when it carries a real link button.

        A resource with no action is a formatted message and nothing more, and
        reporting that as a card is what left ``receipt.degraded`` unable to
        name it.
        """
        del metadata
        chat_id = (
            reply_target_as(event, TelegramReplyTarget).chat_id
            or event.external_channel_id
        )
        reply_parameters = telegram_reply_parameters(event)

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": outbound.display_resource_text(render_plan),
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
                            "text": outbound.button_text(action.label),
                            "url": action.url,
                        }
                    ]
                ]
            }

        await self._call_with_retry("sendMessage", payload)
        return action is not None

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
        data = self._upload_fields(event, caption)
        if not self._bot_token or data is None:
            return False
        await outbound.send_file(
            self._client,
            self._retry_policy,
            data=data,
            file_name=file_name,
            content=file_bytes,
            mime_type=mime_type,
        )
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
        data = self._upload_fields(event, caption)
        if not self._bot_token or data is None:
            return False
        await outbound.upload(
            self._client,
            self._retry_policy,
            "sendVoice",
            "voice",
            data=data,
            file_name=file_name,
            content=audio_bytes,
            mime_type=mime_type or "audio/ogg",
        )
        return True

    def _upload_fields(
        self, event: ParsedInboundSurfaceEvent, caption: str | None
    ) -> dict[str, Any] | None:
        """The form fields an upload carries, or ``None`` when there is no chat."""
        chat_id = (
            reply_target_as(event, TelegramReplyTarget).chat_id
            or event.external_channel_id
        )
        if not chat_id:
            return None
        data: dict[str, Any] = {"chat_id": chat_id}
        if caption:
            data["caption"] = outbound.caption(caption)
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        return data

    async def add_processing_indicator(
        self,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del metadata
        chat_id = (
            reply_target_as(event, TelegramReplyTarget).chat_id
            or event.external_channel_id
        )
        payload: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        thread_id = self._message_thread_id(event)
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        # Not swallowed: every caller already treats a failure as best-effort
        # (`show_typing` answers False and the refresh loop stops), and this
        # catch made `show_typing` unable to ever answer False -- on a dead API
        # the loop ran its full fifteen minutes, one failed call every 4 s.
        await self._client.call("sendChatAction", payload)

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: ProgressHandle | None = None,
    ) -> ProgressHandle | None:
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
        progress_handle: ProgressHandle | None = None,
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
        async with outbound_http(60.0) as client:
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

    def _telegram_metadata(
        self,
        ctx: RunContext[ToolContext],
    ) -> TelegramSurfaceEventMetadata | None:
        metadata = ctx.deps.surface_metadata
        if isinstance(metadata, TelegramSurfaceEventMetadata):
            return metadata
        return None

    def _current_message_attachments(
        self,
        ctx: RunContext[ToolContext],
    ) -> list[TelegramFileAttachment]:
        metadata = self._telegram_metadata(ctx)
        if metadata is None:
            return []
        return common.coerce_attachments(metadata.attachments, TelegramFileAttachment)
