from __future__ import annotations

import mimetypes
from typing import Any

import httpx
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

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
from app.modules.agent_surfaces.platforms.common import (
    payload_first,
    payload_text,
)
from app.modules.agent_surfaces.platforms.rendering import chunk_text

from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
    fallback_text,
    feedback_actions_block,
    markdown_block,
)
from app.modules.agent_surfaces.platforms.slack.channel_reads import (
    SlackChannelReadsMixin,
)
from app.modules.agent_surfaces.platforms.slack.message_blocks import (
    _approval_blocks,
    _display_resource_blocks,
    _progress_status_text,
    _question_blocks,
    slack_acknowledgement_body,
)
from app.modules.agent_surfaces.platforms.delivery import RetryPolicy, with_retry
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    classify_slack_error,
    slack_access_token,
    slack_customized_message_kwargs,
    slack_retry_after,
    slack_scopes,
)
from app.core.log.log import get_logger
from app.core.net.capped_read import read_capped
from app.modules.agent_surfaces.platforms.attachment_limits import (
    INBOUND_ATTACHMENT_BYTE_CAP,
)

logger = get_logger(__name__)

#: One ``chat.postMessage`` body. Slack takes a different set of keys per call
#: (blocks, thread_ts, the ``chat:write.customize`` identity pair), and the
#: values are whatever that key's shape is, so the value type stays open --
#: naming the mapping is what the retry helper needs.
type SlackMessagePayload = dict[str, object]


def _ephemeral_target(metadata: dict[str, object] | None) -> str:
    """Who a reply should be shown to alone, or "" for the whole channel.

    Set only by the fallback path, which answers a newcomer in a channel
    without making the channel read it.
    """
    return str((metadata or {}).get("ephemeral_to") or "").strip()


class SlackPlatformService(SlackChannelReadsMixin):
    def __init__(self, *, credentials: dict[str, Any], parser=None) -> None:
        if parser is None:
            from app.modules.agent_surfaces.platforms.slack.parser import (
                SlackMessageParser,
            )

            parser = SlackMessageParser()
        self.credentials = credentials
        self.parser = parser
        self._retry_policy = RetryPolicy()

    async def _post_message(
        self, client: AsyncWebClient, payload: SlackMessagePayload
    ) -> None:
        """Post one message, retrying the failures Slack expects us to retry.

        Slack limits ``chat.postMessage`` per channel and answers a throttled
        call with 429 and a ``Retry-After`` header. Without this the throttle
        was caught upstream as a transport error, recorded as UNDELIVERED, and
        the answer was never sent again -- and a chunked answer left the person
        reading the first part of something the system had written off. Uses
        the same ``with_retry`` seam Telegram does, so there is one retry
        policy across the platforms rather than two.

        Carrying an ``ephemeral_user`` sends it to that one person instead, for
        the fallback path answering a newcomer in a channel the rest of which
        should not have to read it. An ephemeral is not a stored message -- it
        does not survive a reload and Slack does not guarantee it was shown --
        which is the right trade for telling somebody how to get access and the
        wrong one for anything that has to be answered later.
        """
        user = str(payload.pop("ephemeral_user", "") or "")
        send = (
            (lambda: client.chat_postEphemeral(user=user, **payload))
            if user
            else (lambda: client.chat_postMessage(**payload))
        )
        await with_retry(
            send,
            policy=self._retry_policy,
            classify=classify_slack_error,
            retry_after=slack_retry_after,
        )

    async def fetch_sender_profile(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
    ) -> SurfaceSenderProfile | None:
        user_id = event.sender_external_user_id
        token = slack_access_token(self.credentials)
        if not user_id or not token:
            logger.debug(
                "agent_surfaces.service.slack_fetch_sender_profile_skipped.diagnostic",
                user_id=user_id,
            )
            return None

        client = await build_slack_client(self.credentials)
        try:
            response = await client.users_info(user=user_id)
            user = response.get("user") or {}
            profile = user.get("profile") or {}
            return SurfaceSenderProfile(
                external_user_id=user.get("id") or user_id,
                email=profile.get("email"),
                phone=profile.get("phone"),
                display_name=profile.get("display_name") or profile.get("real_name"),
                raw_profile=user,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_fetch_sender_profile_user.propagated",
                user_id=user_id,
                exc_info=True,
            )
            raise

    async def get_user_display_name(self, user_id: str) -> str | None:
        """Return a user's Slack display name (best-effort).

        Used to surface the bot's own human-facing name for the reach handle.
        Returns None when the token or user id is missing, or on any API error.
        """
        token = slack_access_token(self.credentials)
        if not user_id or not token:
            return None
        try:
            client = await build_slack_client(self.credentials)
            response = await client.users_info(user=user_id)
            user = response.get("user") or {}
            profile = user.get("profile") or {}
            name = profile.get("display_name") or profile.get("real_name")
            return str(name).strip() or None if name else None
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_get_user_display_name.observed",
                user_id=user_id,
            )
            return None

    async def send_message(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_send_message_skipped_due.diagnostic"
            )
            return

        client = await build_slack_client(self.credentials)
        thread_ts = event.reply_target.get("thread_ts")
        identity_kwargs = slack_customized_message_kwargs(
            self.credentials,
            (metadata or {}).get("agent_display_name"),
            (metadata or {}).get("agent_icon_url"),
        )
        # Slack caps markdown blocks at 12,000 characters *per payload*, so a
        # long answer becomes several messages rather than one truncated one.
        chunks = chunk_text(message, limit=MARKDOWN_BLOCK_CHAR_LIMIT) or [message]
        feedback_callback_id = str((metadata or {}).get("feedback_callback_id") or "")
        # Empty for a normal channel post; `_post_message` decides on it.
        ephemeral_user = _ephemeral_target(metadata)
        try:
            for index, chunk in enumerate(chunks):
                blocks: list[dict[str, Any]] = [markdown_block(chunk)]
                # Feedback rates the answer, so it belongs on the last message
                # of a chunked answer — not on every part of one.
                if feedback_callback_id and index == len(chunks) - 1:
                    blocks.append(feedback_actions_block(feedback_callback_id))
                payload: dict[str, Any] = {
                    "channel": channel,
                    "text": fallback_text(chunk),
                    "blocks": blocks,
                }
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                payload.update(identity_kwargs)
                payload["ephemeral_user"] = ephemeral_user
                await self._post_message(client, payload)
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_send_message_channel_s.propagated",
                exc_info=True,
            )
            raise

    async def acknowledge_interaction(
        self,
        interaction: ParsedSurfaceInteraction,
        *,
        text: str | None,
        show_alert: bool,
        clear_actions: bool,
    ) -> None:
        """Answer a tapped button where it was tapped.

        ``response_url`` is the right instrument for this: Slack mints one per
        interaction, it needs no token and no scope, and ``replace_original``
        rewrites the very message the button was in. It expires after 30
        minutes, which is longer than any tap-to-decision gap.

        Best-effort by construction. The decision has already been recorded by
        the time this runs, so a failed acknowledgement must never raise and
        undo it -- but until this existed, a tap on Slack produced no
        confirmation, left the buttons live, and reported a failure to nobody.
        """
        del show_alert  # Slack has no modal alert outside a trigger_id flow.
        response_url = str(
            (interaction.raw_payload or {}).get("response_url") or ""
        ).strip()
        if not response_url:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as http_client:
                await http_client.post(
                    response_url,
                    json=slack_acknowledgement_body(
                        (interaction.raw_payload or {}).get("message") or {},
                        text=text,
                        clear_actions=clear_actions,
                    ),
                )
        except httpx.HTTPError, httpx.InvalidURL:
            logger.debug(
                "agent_surfaces.service.slack_interaction_acknowledgement_best.observed"
            )

    async def _render_resource(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            logger.debug(
                "agent_surfaces.service.slack_send_display_resource_skipped.diagnostic"
            )
            return

        client = await build_slack_client(self.credentials)
        try:
            payload: dict[str, Any] = {
                "channel": channel,
                # Notification-fallback text only (blocks carry the button). Use
                # the caption so the raw URL isn't surfaced in the push preview.
                "text": render_plan.to_caption(),
                "blocks": _display_resource_blocks(render_plan),
            }
            thread_ts = event.reply_target.get("thread_ts")
            if thread_ts:
                payload["thread_ts"] = thread_ts
            payload.update(
                slack_customized_message_kwargs(
                    self.credentials,
                    (metadata or {}).get("agent_display_name"),
                )
            )
            await self._post_message(client, payload)
        except Exception:
            logger.debug(
                "agent_surfaces.service.slack_send_display_resource_channel.propagated",
                exc_info=True,
            )
            raise

    async def _render_choices(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        question_plan: SurfaceQuestionRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render ask_user questions as Block Kit selects + a Submit button."""
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            return False
        client = await build_slack_client(self.credentials)
        payload: dict[str, Any] = {
            "channel": channel,
            "text": question_plan.title,
            "blocks": _question_blocks(question_plan),
        }
        thread_ts = event.reply_target.get("thread_ts")
        if thread_ts:
            payload["thread_ts"] = thread_ts
        payload.update(
            slack_customized_message_kwargs(
                self.credentials, (metadata or {}).get("agent_display_name")
            )
        )
        await self._post_message(client, payload)
        return True

    async def _render_decision(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        approval_plan: SurfaceApprovalRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Render a request_approval prompt as Block Kit Approve/Deny buttons."""
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            return False
        client = await build_slack_client(self.credentials)
        payload: dict[str, Any] = {
            "channel": channel,
            "text": f"Approval needed: {approval_plan.title}",
            "blocks": _approval_blocks(approval_plan),
        }
        thread_ts = event.reply_target.get("thread_ts")
        if thread_ts:
            payload["thread_ts"] = thread_ts
        payload.update(
            slack_customized_message_kwargs(
                self.credentials, (metadata or {}).get("agent_display_name")
            )
        )
        await self._post_message(client, payload)
        return True

    async def add_processing_indicator(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        timestamp = event.external_message_id
        thread_ts = event.reply_target.get("thread_ts")
        if not token or not channel or not timestamp:
            logger.debug(
                "agent_surfaces.service.slack_add_processing_indicator_skipped.diagnostic"
            )
            return

        client = await build_slack_client(self.credentials)
        try:
            if (
                event.is_dm
                and thread_ts
                and "assistant:write" in slack_scopes(self.credentials)
            ):
                status_text, loading_text = _progress_status_text(metadata)
                try:
                    await client.assistant_threads_setStatus(
                        channel_id=str(channel),
                        thread_ts=str(thread_ts),
                        status=status_text,
                        loading_messages=[loading_text],
                    )
                    return
                except SlackApiError as exc:
                    error_code = str((exc.response or {}).get("error") or "")
                    if error_code in {
                        "missing_scope",
                        "invalid_arguments",
                        "method_not_supported_for_channel_type",
                    }:
                        # Not an assistant thread (or the scope was revoked) —
                        # fall through to the reaction below rather than
                        # returning, or the DM shows no indicator at all while
                        # the agent works.
                        logger.debug(
                            "agent_surfaces.service.slack_typing_indicator_unsupported_channel.diagnostic",
                            error_code=error_code,
                        )
                    else:
                        raise
            await client.reactions_add(
                channel=str(channel),
                name="eyes",
                timestamp=str(timestamp),
            )
        except SlackApiError as exc:
            error_code = str((exc.response or {}).get("error") or "")
            if error_code in {"already_reacted", "missing_scope", "not_reactable"}:
                logger.debug(
                    "agent_surfaces.service.slack_reaction_indicator_skipped_channel.diagnostic",
                    error_code=error_code,
                )
                return
            logger.debug(
                "agent_surfaces.service.slack_add_processing_indicator_channel.propagated",
                exc_info=True,
            )
            raise

    async def download_attachment_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        attachment: dict[str, Any],
    ) -> tuple[bytes, str, str] | None:
        """Download a single inbound Slack attachment (no RunContext)."""
        del event
        token = slack_access_token(self.credentials)
        if not token:
            return None
        download_url, file_item = await self._resolve_attachment_url(attachment)
        if not download_url:
            return None
        file_name = self._attachment_file_name(attachment, file_item, download_url)
        content = await self._download_capped(download_url, token)
        return (
            content,
            file_name,
            _attachment_mime_type(attachment, file_item, file_name),
        )

    async def _resolve_attachment_url(
        self, attachment: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """The URL to fetch, asking Slack for it when the event carried none."""
        download_url = payload_text(attachment, "download_url").strip()
        if download_url:
            return download_url, {}
        file_id = payload_text(attachment, "id").strip()
        if not file_id:
            return "", {}
        client = await build_slack_client(self.credentials)
        response = await client.files_info(file=file_id)
        file_item = response.get("file") or {}
        return (
            payload_first(file_item, "url_private_download", "url_private").strip(),
            file_item,
        )

    def _attachment_file_name(
        self,
        attachment: dict[str, Any],
        file_item: dict[str, Any],
        download_url: str,
    ) -> str:
        """A name for the file: from the event, from Slack, or from the URL."""
        return (
            payload_text(attachment, "name").strip()
            or payload_text(file_item, "name").strip()
            or self._filename_from_url(download_url)
            or "slack_file"
        )

    async def _download_capped(self, url: str, token: str) -> bytes:
        """Fetch the file, streamed and capped.

        The caller checked ``attachment["size"]`` first, but Slack does not
        always send one, and a declared size is the sender's claim rather than a
        limit on what actually arrives.
        """
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            async with http_client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {token}"},
            ) as response:
                response.raise_for_status()
                return await read_capped(
                    response.aiter_bytes(), max_bytes=INBOUND_ATTACHMENT_BYTE_CAP
                )

    async def send_file_bytes(
        self,
        event: ParsedInboundSurfaceEvent,
        *,
        file_name: str,
        file_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> bool:
        """Upload + share raw file bytes to the inbound channel (egress)."""
        del mime_type  # Slack infers the type from the filename.
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            return False
        thread_ts = event.reply_target.get("thread_ts")
        client = await build_slack_client(self.credentials)
        upload_ticket = await client.files_getUploadURLExternal(
            filename=file_name, length=len(file_bytes)
        )
        upload_url = str(upload_ticket["upload_url"])
        file_id = str(upload_ticket["file_id"])
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            upload_response = await http_client.post(
                upload_url, files={"file": (file_name, file_bytes)}
            )
            upload_response.raise_for_status()

        completion_payload: dict[str, Any] = {
            "files": [{"id": file_id, "title": caption or file_name}],
            "channel_id": channel,
        }
        if caption:
            completion_payload["initial_comment"] = caption
        if thread_ts:
            completion_payload["thread_ts"] = thread_ts
        completion_payload.update(
            slack_customized_message_kwargs(self.credentials, None)
        )
        try:
            await client.files_completeUploadExternal(**completion_payload)
        except SlackApiError as exc:
            if not _slack_rejected_customized_identity(exc):
                raise
            fallback_payload: dict[str, Any] = {
                "files": completion_payload["files"],
                "channel_id": channel,
            }
            if thread_ts:
                fallback_payload["thread_ts"] = thread_ts
            await client.files_completeUploadExternal(**fallback_payload)
        return True

    def _filename_from_url(self, value: str) -> str:
        return str(value or "").rstrip("/").split("/")[-1].strip()


def _slack_rejected_customized_identity(exc: SlackApiError) -> bool:
    error_code = str((exc.response or {}).get("error") or "")
    if error_code in {"invalid_arguments", "invalid_arg_name"}:
        return True
    messages = (exc.response or {}).get("response_metadata", {}).get("messages") or []
    return any("username" in str(message).lower() for message in messages)


def _attachment_mime_type(
    attachment: dict[str, Any], file_item: dict[str, Any], file_name: str
) -> str:
    """The content type: from the event, from Slack, or guessed from the name."""
    return (
        payload_first(attachment, "mime_type", "content_type").strip()
        or payload_text(file_item, "mimetype").strip()
        or mimetypes.guess_type(file_name)[0]
        or "application/octet-stream"
    )
