from __future__ import annotations

import mimetypes
from typing import Any

import httpx
from pydantic_ai.tools import RunContext
from slack_sdk.errors import SlackApiError

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX as _OTHER_SUFFIX,
    SurfaceChannelInfo,
    SurfaceContextMessage,
    SurfaceDisplayRenderPlan,
    SurfaceQuestion,
    SurfaceQuestionRenderPlan,
    SurfaceSenderProfile,
)
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    SlackSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.common import (
    background_channel_context_note,
    channel_author_label,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_customized_message_kwargs,
    slack_scopes,
)
from app.modules.agent_surfaces.platforms.slack.models import (
    SLACK_FORM_SUBMIT_ACTION_ID,
    SlackChannelMessageSnapshot,
    SlackFileAttachment,
    SlackRecentChannelMessagesParams,
    SlackRecentChannelMessagesResult,
    SlackSearchChannelMessagesParams,
    SlackSearchChannelMessagesResult,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class SlackPlatformService:
    def __init__(self, *, credentials: dict[str, Any], parser=None) -> None:
        if parser is None:
            from app.modules.agent_surfaces.platforms.slack.parser import (
                SlackMessageParser,
            )

            parser = SlackMessageParser()
        self.credentials = credentials
        self.parser = parser

    async def fetch_sender_profile(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
    ) -> SurfaceSenderProfile | None:
        user_id = event.sender_external_user_id
        token = slack_access_token(self.credentials)
        if not user_id or not token:
            logger.warning(
                "Slack fetch_sender_profile skipped due to missing user_id or token user_id=%s",
                user_id,
            )
            return None

        client = build_slack_client(self.credentials)
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
        except Exception as exc:
            logger.exception(
                "Slack fetch_sender_profile failed for user=%s: %s", user_id, exc
            )
            raise

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
            logger.warning(
                "Slack send_message skipped due to missing token or channel channel=%s",
                channel,
            )
            return

        client = build_slack_client(self.credentials)
        try:
            payload: dict[str, Any] = {"channel": channel, "text": message}
            thread_ts = event.reply_target.get("thread_ts")
            if thread_ts:
                payload["thread_ts"] = thread_ts
            payload.update(
                slack_customized_message_kwargs(
                    self.credentials,
                    (metadata or {}).get("agent_display_name"),
                )
            )
            await client.chat_postMessage(**payload)
        except Exception as exc:
            logger.exception(
                "Slack send_message failed channel=%s thread=%s: %s",
                channel,
                event.reply_target.get("thread_ts"),
                exc,
            )
            raise

    async def send_display_resource(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        render_plan: SurfaceDisplayRenderPlan,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            logger.warning(
                "Slack send_display_resource skipped due to missing token or channel channel=%s",
                channel,
            )
            return

        client = build_slack_client(self.credentials)
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
            await client.chat_postMessage(**payload)
        except Exception as exc:
            logger.exception(
                "Slack send_display_resource failed channel=%s thread=%s: %s",
                channel,
                event.reply_target.get("thread_ts"),
                exc,
            )
            raise

    async def send_questions(
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
        client = build_slack_client(self.credentials)
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
        await client.chat_postMessage(**payload)
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
            logger.warning(
                "Slack add_processing_indicator skipped channel=%s timestamp=%s",
                channel,
                timestamp,
            )
            return

        client = build_slack_client(self.credentials)
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
                        logger.warning(
                            "Slack typing indicator unsupported channel=%s error=%s",
                            channel,
                            error_code,
                        )
                        return
                    raise
            await client.reactions_add(
                channel=str(channel),
                name="eyes",
                timestamp=str(timestamp),
            )
        except SlackApiError as exc:
            error_code = str((exc.response or {}).get("error") or "")
            if error_code in {"already_reacted", "missing_scope", "not_reactable"}:
                logger.warning(
                    "Slack reaction indicator skipped channel=%s timestamp=%s error=%s",
                    channel,
                    timestamp,
                    error_code,
                )
                return
            logger.exception(
                "Slack add_processing_indicator failed channel=%s timestamp=%s: %s",
                channel,
                timestamp,
                exc,
            )
            raise

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Post/edit a live progress message via chat.update; return its handle.

        First call posts a placeholder; subsequent calls edit it in place. The
        handle carries the message ts + channel so ``end_progress`` can delete it.
        Best-effort: rate limits / API errors keep the prior handle.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel:
            return progress_handle
        client = build_slack_client(self.credentials)
        text = _truncate_slack_text(f"⏳ {progress_text}", 3000) or "⏳ Working…"
        try:
            if progress_handle and progress_handle.get("ts"):
                await client.chat_update(
                    channel=str(progress_handle.get("channel") or channel),
                    ts=str(progress_handle["ts"]),
                    text=text,
                )
                return progress_handle
            payload: dict[str, Any] = {"channel": str(channel), "text": text}
            thread_ts = event.reply_target.get("thread_ts")
            if thread_ts:
                payload["thread_ts"] = thread_ts
            response = await client.chat_postMessage(**payload)
            ts = str(response["ts"])
            return {"ts": ts, "channel": str(response.get("channel") or channel)}
        except SlackApiError as exc:
            logger.warning(
                "Slack stream_progress failed channel=%s error=%s",
                channel,
                str((exc.response or {}).get("error") or exc),
            )
            return progress_handle

    async def end_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Delete the streaming progress placeholder so it doesn't linger next to
        the final answer. Best-effort."""
        if not progress_handle or not progress_handle.get("ts"):
            return
        token = slack_access_token(self.credentials)
        if not token:
            return
        client = build_slack_client(self.credentials)
        channel = progress_handle.get("channel") or event.reply_target.get("channel")
        try:
            await client.chat_delete(channel=str(channel), ts=str(progress_handle["ts"]))
        except SlackApiError as exc:
            logger.warning(
                "Slack end_progress delete failed channel=%s error=%s",
                channel,
                str((exc.response or {}).get("error") or exc),
            )

    async def list_channels(self) -> list[SurfaceChannelInfo]:
        """List Slack public/private channels for configuring channel routes."""
        client = build_slack_client(self.credentials)
        channels: list[SurfaceChannelInfo] = []
        cursor: str | None = None
        for _ in range(20):  # bounded pagination safety
            response = await client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            for item in response.get("channels") or []:
                channel_id = str((item or {}).get("id") or "").strip()
                if not channel_id:
                    continue
                channels.append(
                    SurfaceChannelInfo(
                        id=channel_id,
                        name=item.get("name"),
                        is_member=item.get("is_member"),
                    )
                )
            cursor = (
                str(
                    (response.get("response_metadata") or {}).get("next_cursor") or ""
                ).strip()
                or None
            )
            if not cursor:
                break
        return channels

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
        download_url = str(attachment.get("download_url") or "").strip()
        file_id = str(attachment.get("id") or "").strip()
        file_item: dict[str, Any] = {}
        if not download_url and file_id:
            client = build_slack_client(self.credentials)
            response = await client.files_info(file=file_id)
            file_item = response.get("file") or {}
            download_url = str(
                file_item.get("url_private_download")
                or file_item.get("url_private")
                or ""
            ).strip()
        if not download_url:
            return None
        file_name = (
            str(attachment.get("name") or "").strip()
            or str(file_item.get("name") or "").strip()
            or self._filename_from_url(download_url)
            or "slack_file"
        )
        async with httpx.AsyncClient(timeout=60.0) as http_client:
            response = await http_client.get(
                download_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            content = response.content
        mime_type = (
            str(attachment.get("mime_type") or attachment.get("content_type") or "")
            .strip()
            or str(file_item.get("mimetype") or "").strip()
            or mimetypes.guess_type(file_name)[0]
            or "application/octet-stream"
        )
        return content, file_name, mime_type

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
        client = build_slack_client(self.credentials)
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

    async def get_recent_channel_messages(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: SlackRecentChannelMessagesParams,
    ) -> SlackRecentChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.deps.external_channel_id
        if not token or not channel:
            logger.warning(
                "Slack get_recent_channel_messages missing context channel=%s conversation=%s",
                channel,
                ctx.deps.conversation_id,
            )
            return SlackRecentChannelMessagesResult(
                success=False,
                error="Slack conversation context is missing channel credentials.",
            )

        try:
            client = build_slack_client(self.credentials)
            response = await client.conversations_history(
                **_build_channel_history_kwargs(
                    channel=str(channel),
                    limit=request.limit,
                    current_thread_id=ctx.deps.external_thread_id,
                    include_current_thread=request.include_current_thread,
                )
            )
            messages = self._normalize_slack_messages(
                response.get("messages") or [],
                current_thread_id=ctx.deps.external_thread_id,
                include_current_thread=request.include_current_thread,
            )
            return SlackRecentChannelMessagesResult(
                success=True,
                message=background_channel_context_note(len(messages)),
                messages=messages,
            )
        except Exception as exc:
            logger.exception(
                "Slack get_recent_channel_messages failed channel=%s conversation=%s: %s",
                channel,
                ctx.deps.conversation_id,
                exc,
            )
            raise

    async def fetch_recent_context(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        limit: int = 15,
    ) -> list[SurfaceContextMessage]:
        """Recent thread/channel messages for background context on a mention.

        Uses conversations.replies inside a thread, else conversations.history.
        Best-effort: missing creds / API errors yield an empty list.
        """
        token = slack_access_token(self.credentials)
        channel = event.external_channel_id
        if not token or not channel:
            return []
        thread_ts = event.external_thread_id
        try:
            client = build_slack_client(self.credentials)
            if thread_ts and str(thread_ts) != str(channel):
                response = await client.conversations_replies(
                    channel=str(channel), ts=str(thread_ts), limit=limit
                )
                raw = list(response.get("messages") or [])  # oldest-first
            else:
                response = await client.conversations_history(
                    channel=str(channel), limit=limit
                )
                # history is newest-first → flip to chronological
                raw = list(reversed(response.get("messages") or []))
        except Exception as exc:
            logger.warning(
                "Slack fetch_recent_context failed channel=%s: %s", channel, exc
            )
            return []

        current_ts = str(event.external_message_id or "")
        out: list[SurfaceContextMessage] = []
        for item in raw[-limit:]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ts = str(item.get("ts") or "")
            if current_ts and ts == current_ts:
                continue  # the message being handled isn't "context"
            author = (
                str(item.get("user") or item.get("username") or "").strip() or None
            )
            out.append(SurfaceContextMessage(author=author, text=text, ts=ts or None))
        return out

    async def search_current_channel(
        self,
        *,
        ctx: RunContext[ConversationContext],
        request: SlackSearchChannelMessagesParams,
    ) -> SlackSearchChannelMessagesResult:
        token = slack_access_token(self.credentials)
        channel = ctx.deps.external_channel_id
        if not token or not channel:
            logger.warning(
                "Slack search_current_channel missing context channel=%s conversation=%s",
                channel,
                ctx.deps.conversation_id,
            )
            return SlackSearchChannelMessagesResult(
                success=False,
                error="Slack conversation context is missing channel credentials.",
            )

        try:
            client = build_slack_client(self.credentials)
            matches: list[SlackChannelMessageSnapshot] = []
            cursor: str | None = None
            remaining = request.scan_limit
            query = request.query.strip().lower()
            if not query:
                return SlackSearchChannelMessagesResult(
                    success=False,
                    error="Query cannot be empty.",
                )

            while remaining > 0 and len(matches) < request.limit:
                batch_size = min(100, remaining)
                history_kwargs = _build_channel_history_kwargs(
                    channel=str(channel),
                    limit=batch_size,
                    current_thread_id=ctx.deps.external_thread_id,
                    include_current_thread=request.include_current_thread,
                    cursor=cursor,
                )
                response = await client.conversations_history(**history_kwargs)
                normalized_batch = self._normalize_slack_messages(
                    response.get("messages") or [],
                    current_thread_id=ctx.deps.external_thread_id,
                    include_current_thread=request.include_current_thread,
                )
                for item in normalized_batch:
                    remaining -= 1
                    if query in item.text.lower():
                        matches.append(item)
                        if len(matches) >= request.limit:
                            break
                    if remaining <= 0:
                        break

                cursor = (
                    str(
                        (response.get("response_metadata") or {}).get("next_cursor")
                        or ""
                    ).strip()
                    or None
                )
                if not cursor:
                    break

            return SlackSearchChannelMessagesResult(
                success=True,
                message=background_channel_context_note(len(matches)),
                matches=matches,
            )
        except Exception as exc:
            logger.exception(
                "Slack search_current_channel failed channel=%s conversation=%s query=%s: %s",
                channel,
                ctx.deps.conversation_id,
                request.query,
                exc,
            )
            raise

    def _current_message_attachments(
        self,
        ctx: RunContext[ConversationContext],
    ) -> list[SlackFileAttachment]:
        metadata = ctx.deps.surface_metadata
        if not isinstance(metadata, SlackSurfaceEventMetadata):
            return []
        return list(metadata.attachments)

    def _normalize_slack_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        current_thread_id: str | None,
        include_current_thread: bool,
    ) -> list[SlackChannelMessageSnapshot]:
        normalized: list[SlackChannelMessageSnapshot] = []
        for item in reversed(messages):
            if not isinstance(item, dict):
                continue
            parsed = (
                self.parser.normalize_context_message(item) if self.parser else None
            )
            if parsed is None:
                continue
            snapshot = SlackChannelMessageSnapshot.model_validate(parsed)
            if (
                not include_current_thread
                and current_thread_id
                and snapshot.thread_ts == current_thread_id
            ):
                continue
            if snapshot.author_label is None:
                snapshot.author_label = channel_author_label(
                    snapshot.display_name, snapshot.user
                )
            normalized.append(snapshot)
        return normalized

    def _filename_from_url(self, value: str) -> str:
        return str(value or "").rstrip("/").split("/")[-1].strip()


def _slack_rejected_customized_identity(exc: SlackApiError) -> bool:
    error_code = str((exc.response or {}).get("error") or "")
    if error_code in {"invalid_arguments", "invalid_arg_name"}:
        return True
    messages = (exc.response or {}).get("response_metadata", {}).get("messages") or []
    return any("username" in str(message).lower() for message in messages)


def _progress_status_text(metadata: dict[str, Any] | None) -> tuple[str, str]:
    progress_text = (metadata or {}).get("progress_text")
    if isinstance(progress_text, str) and progress_text.strip():
        text = progress_text.strip()
        return text, text
    return "is taking a look...", "Taking a look..."


def _question_select_element(question: SurfaceQuestion) -> dict[str, Any] | None:
    """A single/multi static_select whose option values are the option labels.

    The block_id is the question header, so the flattened submission comes back
    keyed by header → the chosen option label(s), ready for AskUserResponse.
    """
    options = [
        {
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(
                    f"{opt.label} (recommended)" if opt.recommended else opt.label,
                    74,
                )
                or "—",
            },
            "value": opt.label,
        }
        for opt in question.options[:100]
    ]
    if not options:
        return None
    return {
        "type": (
            "multi_static_select"
            if question.multi_select
            else "static_select"
        ),
        "action_id": question.header,
        "options": options,
    }


def _question_blocks(plan: SurfaceQuestionRenderPlan) -> list[dict[str, Any]]:
    """Build Block Kit select blocks (+ optional Other text) + a Submit button."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(plan.title, 150) or "Questions",
            },
        }
    ]
    for question in plan.questions:
        element = _question_select_element(question)
        if element is None:
            continue
        blocks.append(
            {
                "type": "input",
                "block_id": question.header,
                "optional": True,
                "label": {
                    "type": "plain_text",
                    "text": _truncate_slack_text(question.question, 150)
                    or question.header,
                },
                "element": element,
            }
        )
        if plan.allow_other:
            blocks.append(
                {
                    "type": "input",
                    "block_id": f"{question.header}{_OTHER_SUFFIX}",
                    "optional": True,
                    "label": {
                        "type": "plain_text",
                        "text": "Other (type your own)",
                    },
                    "element": {
                        "type": "plain_text_input",
                        "action_id": f"{question.header}{_OTHER_SUFFIX}",
                    },
                }
            )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": SLACK_FORM_SUBMIT_ACTION_ID,
                    "style": "primary",
                    "text": {
                        "type": "plain_text",
                        "text": _truncate_slack_text(plan.submit_label, 74) or "Submit",
                    },
                    "value": plan.callback_id,
                }
            ],
        }
    )
    return blocks


def _display_resource_blocks(
    render_plan: SurfaceDisplayRenderPlan,
) -> list[dict[str, Any]]:
    text_parts = [f"*{_slack_escape(render_plan.title)}*"]
    if render_plan.summary:
        text_parts.append(_slack_escape(render_plan.summary))
    for line in render_plan.detail_lines[:4]:
        text_parts.append(f"> {_slack_escape(line)}")

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_slack_text("\n".join(text_parts), 2900),
            },
        }
    ]
    action = render_plan.primary_action
    if action is not None:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": _truncate_slack_text(action.label, 75),
                        },
                        "url": action.url,
                    }
                ],
            }
        )
    return blocks


def _slack_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate_slack_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "..."


def _build_channel_history_kwargs(
    *,
    channel: str,
    limit: int,
    current_thread_id: str | None,
    include_current_thread: bool,
    cursor: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"channel": channel, "limit": limit}
    if cursor:
        kwargs["cursor"] = cursor
        return kwargs
    if current_thread_id and not include_current_thread and not channel.startswith("D"):
        kwargs["latest"] = current_thread_id
        kwargs["inclusive"] = False
    return kwargs
