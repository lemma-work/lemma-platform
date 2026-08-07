from __future__ import annotations

import mimetypes
from typing import Any

import httpx
from pydantic_ai.tools import RunContext
from slack_sdk.errors import SlackApiError

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.models import (
    OTHER_ANSWER_SUFFIX as _OTHER_SUFFIX,
    SurfaceApprovalRenderPlan,
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
from app.modules.agent_surfaces.platforms.rendering import chunk_text
from app.modules.agent_surfaces.platforms.slack.blocks import (
    MARKDOWN_BLOCK_CHAR_LIMIT,
    app_home_view,
    dm_agent_modal,
    channel_setup_confirmation_blocks,
    channel_setup_modal,
    channel_setup_prompt_blocks,
    fallback_text,
    feedback_actions_block,
    markdown_block,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    build_slack_client,
    slack_access_token,
    slack_customized_message_kwargs,
    slack_scopes,
)
from app.modules.agent_surfaces.platforms.slack.models import (
    SLACK_APPROVAL_ACTION_ID_BY_DECISION,
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
            logger.debug(
                'agent_surfaces.service.slack_fetch_sender_profile_skipped.diagnostic',
                user_id=user_id,
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
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_fetch_sender_profile_user.propagated',
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
            client = build_slack_client(self.credentials)
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
                'agent_surfaces.service.slack_send_message_skipped_due.diagnostic'
            )
            return

        client = build_slack_client(self.credentials)
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
                await client.chat_postMessage(**payload)
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_send_message_channel_s.propagated',
                exc_info=True,
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
            logger.debug(
                'agent_surfaces.service.slack_send_display_resource_skipped.diagnostic'
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
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_send_display_resource_channel.propagated',
                exc_info=True,
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

    async def send_approval(
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
        client = build_slack_client(self.credentials)
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
            logger.debug(
                'agent_surfaces.service.slack_add_processing_indicator_skipped.diagnostic'
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
                        # Not an assistant thread (or the scope was revoked) —
                        # fall through to the reaction below rather than
                        # returning, or the DM shows no indicator at all while
                        # the agent works.
                        logger.debug(
                            'agent_surfaces.service.slack_typing_indicator_unsupported_channel.diagnostic',
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
                    'agent_surfaces.service.slack_reaction_indicator_skipped_channel.diagnostic',
                    error_code=error_code,
                )
                return
            logger.debug(
                'agent_surfaces.service.slack_add_processing_indicator_channel.propagated',
                exc_info=True,
            )
            raise

    async def send_channel_setup_prompt(
        self,
        *,
        channel_id: str,
        user_id: str,
        channel_name: str | None = None,
        confirmed_agent: str | None = None,
    ) -> bool:
        """Ask the person who just added Lemma who should answer here.

        Ephemeral, so an unconfigured channel never gets bot noise in front of
        everyone. Returns False when there is nobody to ask (Slack records no
        inviter when the bot joins itself via ``chat:write.public``).
        """
        token = slack_access_token(self.credentials)
        if not token or not channel_id or not user_id:
            return False
        try:
            await build_slack_client(self.credentials).chat_postEphemeral(
                channel=str(channel_id),
                user=str(user_id),
                text=(
                    f"{confirmed_agent} now answers in this channel."
                    if confirmed_agent
                    else "Choose which agent answers in this channel."
                ),
                blocks=(
                    channel_setup_confirmation_blocks(
                        channel_name=channel_name, agent_label=confirmed_agent
                    )
                    if confirmed_agent
                    else channel_setup_prompt_blocks(
                        channel_id=str(channel_id), channel_name=channel_name
                    )
                ),
            )
            return True
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_channel_setup_prompt.diagnostic'
            )
            return False

    async def open_channel_setup_modal(
        self,
        *,
        trigger_id: str,
        channel_id: str,
        channel_label: str | None,
        agent_names: list[str],
    ) -> bool:
        """Open the "who answers here?" modal.

        Must be called within ~3 seconds of the button tap: Slack expires the
        trigger_id, and there is no way to reopen it without another tap.
        """
        token = slack_access_token(self.credentials)
        if not token or not trigger_id:
            return False
        try:
            await build_slack_client(self.credentials).views_open(
                trigger_id=trigger_id,
                view=channel_setup_modal(
                    channel_id=channel_id,
                    channel_label=channel_label,
                    agent_names=agent_names,
                ),
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_open_setup_modal.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def send_starter_prompt(self, *, user_id: str, prompt: str) -> bool:
        """Open the DM and drop the starter question in as an ephemeral nudge.

        The point is that a first-time viewer gets a real answer without having
        to think of a question. Slack has no way to *speak as the user*, so this
        opens their DM and shows the prompt to copy — honest about what it is,
        rather than faking a message from them.
        """
        token = slack_access_token(self.credentials)
        if not token or not user_id or not prompt:
            return False
        client = build_slack_client(self.credentials)
        try:
            opened = await client.conversations_open(users=str(user_id))
            channel = ((opened.get("channel") or {}).get("id")) or ""
            if not channel:
                return False
            await client.chat_postEphemeral(
                channel=str(channel),
                user=str(user_id),
                text=prompt,
                blocks=[
                    {
                        "type": "markdown",
                        "text": f"Try asking me:\n\n> {prompt}",
                    }
                ],
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_starter_prompt.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def open_dm_agent_modal(
        self, *, trigger_id: str, agent_names: list[str], current: str | None
    ) -> bool:
        token = slack_access_token(self.credentials)
        if not token or not trigger_id:
            return False
        try:
            await build_slack_client(self.credentials).views_open(
                trigger_id=trigger_id,
                view=dm_agent_modal(agent_names=agent_names, current=current),
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_open_setup_modal.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def publish_home_view(
        self,
        *,
        user_id: str,
        pod_name: str | None,
        dm_agent_name: str | None,
        channel_routes: list,
        agents: list | None = None,
        apps: list | None = None,
        workspace_url: str | None = None,
        logo_url: str | None = None,
    ) -> bool:
        """Publish the Home tab for one person."""
        token = slack_access_token(self.credentials)
        if not token or not user_id:
            return False
        try:
            await build_slack_client(self.credentials).views_publish(
                user_id=str(user_id),
                view=app_home_view(
                    pod_name=pod_name,
                    dm_agent_name=dm_agent_name,
                    channel_routes=channel_routes,
                    agents=agents,
                    apps=apps,
                    workspace_url=workspace_url,
                    logo_url=logo_url,
                ),
            )
            return True
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_publish_home_view.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False

    async def channel_name(self, channel_id: str) -> str | None:
        """Best-effort channel name, so prompts can say #sales not "this channel"."""
        token = slack_access_token(self.credentials)
        if not token or not channel_id:
            return None
        try:
            response = await build_slack_client(self.credentials).conversations_info(
                channel=str(channel_id)
            )
            name = ((response.get("channel") or {}).get("name") or "").strip()
            return name or None
        except SlackApiError:
            return None

    async def set_thread_title(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        title: str,
    ) -> bool:
        """Name the agent thread, so Slack's own DM history is navigable.

        Only meaningful in a DM under the agent messaging experience. Entirely
        best-effort: a workspace on an older install has no ``assistant:write``
        and simply keeps Slack's default thread naming.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        clean_title = _truncate_slack_text(str(title or "").strip(), 250)
        if not token or not channel or not thread_ts or not clean_title:
            return False
        if not event.is_dm or "assistant:write" not in slack_scopes(self.credentials):
            return False
        try:
            await build_slack_client(self.credentials).assistant_threads_setTitle(
                channel_id=str(channel),
                thread_ts=str(thread_ts),
                title=clean_title,
            )
            return True
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_set_thread_title.diagnostic'
            )
            return False

    async def set_suggested_prompts(
        self,
        *,
        event: ParsedInboundSurfaceEvent,
        prompts: list[tuple[str, str]],
        title: str | None = None,
    ) -> bool:
        """Offer tappable openers instead of an empty box.

        ``prompts`` is ``(title, message)`` pairs — the title is the chip label,
        the message is what gets sent when it is tapped. Slack accepts at most
        four.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        if not token or not channel or not prompts:
            return False
        if not event.is_dm or "assistant:write" not in slack_scopes(self.credentials):
            return False
        payload_prompts = [
            {
                "title": _truncate_slack_text(str(prompt_title).strip(), 100),
                "message": str(prompt_message).strip(),
            }
            for prompt_title, prompt_message in prompts[:4]
            if str(prompt_title).strip() and str(prompt_message).strip()
        ]
        if not payload_prompts:
            return False
        kwargs: dict[str, Any] = {
            "channel_id": str(channel),
            "prompts": payload_prompts,
        }
        # Optional since the agent messaging experience shipped; passing it
        # still scopes the prompts to one thread where the app is on the older
        # assistant view.
        thread_ts = event.reply_target.get("thread_ts")
        if thread_ts:
            kwargs["thread_ts"] = str(thread_ts)
        if title:
            kwargs["title"] = _truncate_slack_text(str(title).strip(), 100)
        try:
            await build_slack_client(
                self.credentials
            ).assistant_threads_setSuggestedPrompts(**kwargs)
            return True
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_set_suggested_prompts.diagnostic'
            )
            return False

    async def stream_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_text: str,
        progress_handle: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Open (or extend) a native Slack stream carrying the agent's steps.

        The first call opens a stream with ``chat.startStream``; each later call
        completes the step in flight and appends the next one as a
        ``task_update`` chunk, so Slack renders a collapsible timeline of what
        the agent actually did. ``finish_progress`` closes the same message with
        the final answer, which is why nothing here is ever deleted.

        Best-effort: rate limits / API errors keep the prior handle, and the
        caller falls back to posting the answer as its own message.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        # ``chat.startStream`` is thread-scoped. The parser always sets
        # thread_ts (falling back to the message ts), so this holds in channels
        # and DMs alike — but never stream without one.
        if not token or not channel or not thread_ts:
            return progress_handle
        client = build_slack_client(self.credentials)
        title = _truncate_slack_text(progress_text.strip(), 200) or "Working…"
        try:
            if progress_handle and progress_handle.get("ts"):
                sequence = int(progress_handle.get("task_seq") or 0)
                chunks: list[dict[str, Any]] = []
                if sequence:
                    chunks.append(
                        _task_chunk(sequence, progress_handle.get("task_title"), "complete")
                    )
                sequence += 1
                chunks.append(_task_chunk(sequence, title, "in_progress"))
                await client.chat_appendStream(
                    channel=str(progress_handle.get("channel") or channel),
                    ts=str(progress_handle["ts"]),
                    chunks=chunks,
                )
                return {
                    **progress_handle,
                    "task_seq": sequence,
                    "task_title": title,
                }
            start_payload: dict[str, Any] = {
                "channel": str(channel),
                "thread_ts": str(thread_ts),
                "task_display_mode": "timeline",
            }
            start_payload.update(
                slack_customized_message_kwargs(
                    self.credentials, (metadata or {}).get("agent_display_name")
                )
            )
            response = await client.chat_startStream(**start_payload)
            ts = str(response["ts"])
            resolved_channel = str(response.get("channel") or channel)
            await client.chat_appendStream(
                channel=resolved_channel,
                ts=ts,
                chunks=[_task_chunk(1, title, "in_progress")],
            )
            return {
                "ts": ts,
                "channel": resolved_channel,
                "stream": True,
                "task_seq": 1,
                "task_title": title,
            }
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_stream_progress_channel_s.diagnostic'
            )
            return progress_handle

    async def append_stream_text(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Append model text to a live stream, opening one if needed.

        This is what makes the answer *appear as it is written* rather than
        arriving whole. Opening lazily on the first token means a run that never
        produces text never leaves an empty stream behind, and no placeholder
        step is invented to justify the message existing.
        """
        token = slack_access_token(self.credentials)
        channel = event.reply_target.get("channel")
        thread_ts = event.reply_target.get("thread_ts")
        # An empty text is a request to *open* the stream (run start); only a
        # missing channel/thread makes it impossible.
        if not token or not channel or not thread_ts:
            return progress_handle
        if not text and progress_handle:
            return progress_handle
        client = build_slack_client(self.credentials)
        try:
            if not (progress_handle and progress_handle.get("ts")):
                start_payload: dict[str, Any] = {
                    "channel": str(channel),
                    "thread_ts": str(thread_ts),
                    # Same mode the step stream uses. A stream is either
                    # chunk-based or plain-text for its whole life; mixing the
                    # two is what Slack rejects as streaming_mode_mismatch.
                    "task_display_mode": "timeline",
                }
                start_payload.update(
                    slack_customized_message_kwargs(
                        self.credentials,
                        (metadata or {}).get("agent_display_name"),
                        (metadata or {}).get("agent_icon_url"),
                    )
                )
                response = await client.chat_startStream(**start_payload)
                progress_handle = {
                    "ts": str(response["ts"]),
                    "channel": str(response.get("channel") or channel),
                    "stream": True,
                    "task_seq": 0,
                    "streamed_text": True,
                }
            if not text:
                return progress_handle
            await client.chat_appendStream(
                channel=str(progress_handle.get("channel") or channel),
                ts=str(progress_handle["ts"]),
                chunks=[_markdown_chunk(text)],
            )
            return {**progress_handle, "streamed_text": True}
        except SlackApiError as exc:
            logger.debug(
                'agent_surfaces.service.slack_append_stream_text.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return progress_handle

    async def finish_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Close the live stream *with* the final answer, as one message.

        This is the whole point of streaming: the thinking steps and the answer
        they produced are a single artifact in the channel, instead of a
        placeholder that gets deleted and an answer posted beside it.

        Returns False when there is no live stream to close, so the caller can
        deliver the answer as an ordinary message instead.
        """
        if not progress_handle or not progress_handle.get("ts"):
            return False
        token = slack_access_token(self.credentials)
        # A stream that already carries the answer still needs closing, so an
        # empty message is only a refusal when nothing was streamed.
        if not token or (not message.strip() and not progress_handle.get("streamed_text")):
            return False
        client = build_slack_client(self.credentials)
        channel = str(
            progress_handle.get("channel") or event.reply_target.get("channel") or ""
        )
        if not channel:
            return False
        # The answer must fit the 12k markdown budget; anything beyond it closes
        # the stream and continues as follow-up messages.
        chunks_of_answer = chunk_text(message, limit=MARKDOWN_BLOCK_CHAR_LIMIT) or (
            [message] if message.strip() else []
        )
        sequence = int(progress_handle.get("task_seq") or 0)
        closing_chunks: list[dict[str, Any]] = []
        if sequence:
            closing_chunks.append(
                _task_chunk(sequence, progress_handle.get("task_title"), "complete")
            )
        try:
            # The answer is *appended* and the stream then closed. Slack rejects
            # a stopStream that tries to introduce the body itself, so append is
            # the call that carries text and stop only finalises.
            if chunks_of_answer or closing_chunks:
                append_kwargs: dict[str, Any] = {
                    "channel": channel,
                    "ts": str(progress_handle["ts"]),
                }
                combined = list(closing_chunks)
                if chunks_of_answer:
                    combined.append(_markdown_chunk(chunks_of_answer[0]))
                if combined:
                    append_kwargs["chunks"] = combined
                    await client.chat_appendStream(**append_kwargs)
            await client.chat_stopStream(
                channel=channel,
                ts=str(progress_handle["ts"]),
            )
        except SlackApiError as exc:
            # Say which Slack error it was: this path silently falls back to a
            # plain message, so without the code a failure here is invisible.
            logger.debug(
                'agent_surfaces.service.slack_finish_progress_stop_stream.diagnostic',
                error_code=str((exc.response or {}).get("error") or "unknown"),
            )
            return False
        for remainder in chunks_of_answer[1:] if chunks_of_answer else []:
            await self.send_message(event=event, message=remainder, metadata=metadata)
        return True

    async def end_progress(
        self,
        event: ParsedInboundSurfaceEvent,
        progress_handle: dict[str, Any] | None = None,
    ) -> None:
        """Dispose of a live stream that will never receive an answer.

        The answer is going somewhere else (a plain message, or nowhere at all
        because the run failed), so leaving the stream behind strands a
        "Thinking…" bubble next to the real reply — two messages where the user
        should see one. Close it, then delete it.

        Deleting is best-effort on top of the close: the stream has to be
        stopped before Slack will accept a delete, and if the delete is refused
        a stopped stream is still better than a spinning one.
        """
        if not progress_handle or not progress_handle.get("ts"):
            return
        token = slack_access_token(self.credentials)
        if not token:
            return
        client = build_slack_client(self.credentials)
        channel = progress_handle.get("channel") or event.reply_target.get("channel")
        sequence = int(progress_handle.get("task_seq") or 0)
        try:
            if progress_handle.get("stream"):
                await client.chat_stopStream(
                    channel=str(channel),
                    ts=str(progress_handle["ts"]),
                    chunks=(
                        [
                            _task_chunk(
                                sequence, progress_handle.get("task_title"), "complete"
                            )
                        ]
                        if sequence
                        else None
                    ),
                )
            await client.chat_delete(
                channel=str(channel), ts=str(progress_handle["ts"])
            )
        except SlackApiError:
            logger.debug(
                'agent_surfaces.service.slack_end_progress_delete_channel.diagnostic'
            )

    async def list_channels(self) -> list[SurfaceChannelInfo]:
        """List Slack public/private channels for configuring channel routes.

        Private channels need ``groups:read``, which a workspace installed
        before that scope shipped will not have granted. Slack answers such a
        request with ``missing_scope``, so the first failure retries with public
        channels only rather than leaving the picker empty.
        """
        client = build_slack_client(self.credentials)
        channels: list[SurfaceChannelInfo] = []
        cursor: str | None = None
        channel_types = "public_channel,private_channel"
        for _ in range(20):  # bounded pagination safety
            try:
                response = await client.conversations_list(
                    types=channel_types,
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
            except SlackApiError as exc:
                error_code = str((exc.response or {}).get("error") or "")
                if error_code != "missing_scope" or channel_types == "public_channel":
                    raise
                logger.debug(
                    "agent_surfaces.service.slack_list_channels_private_unavailable.diagnostic",
                    error_code=error_code,
                )
                channel_types = "public_channel"
                continue
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
            str(
                attachment.get("mime_type") or attachment.get("content_type") or ""
            ).strip()
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
            logger.debug(
                'agent_surfaces.service.slack_get_recent_channel_messages.diagnostic',
                conversation_id=ctx.deps.conversation_id,
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
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_get_recent_channel_messages.propagated',
                conversation_id=ctx.deps.conversation_id,
                exc_info=True,
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
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_fetch_recent_context_channel.diagnostic'
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
            author = str(item.get("user") or item.get("username") or "").strip() or None
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
            logger.debug(
                'agent_surfaces.service.slack_search_current_channel_missing.diagnostic',
                conversation_id=ctx.deps.conversation_id,
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
        except Exception:
            logger.debug(
                'agent_surfaces.service.slack_search_current_channel_channel.propagated',
                conversation_id=ctx.deps.conversation_id,
                exc_info=True,
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


def _markdown_chunk(text: str) -> dict[str, Any]:
    """Model text as a stream chunk.

    A stream is either chunk-based or plain-text for its whole life. Because
    the step timeline uses chunks, the answer must be a chunk too — appending
    top-level ``markdown_text`` to a chunk stream is rejected with
    ``streaming_mode_mismatch``.
    """
    return {"type": "markdown_text", "text": text}


def _task_chunk(sequence: int, title: str | None, status: str) -> dict[str, Any]:
    """One step of the agent's work, as a Slack ``task_update`` chunk.

    The id is stable per step so appending the same id with ``complete`` closes
    the step already on screen rather than adding a second one.
    """
    return {
        "type": "task_update",
        "id": f"step-{sequence}",
        "title": _truncate_slack_text(str(title or "Working…"), 200) or "Working…",
        "status": status,
    }


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
        "type": ("multi_static_select" if question.multi_select else "static_select"),
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


def _approval_blocks(plan: SurfaceApprovalRenderPlan) -> list[dict[str, Any]]:
    """Build a section (title/reason/action) + Approve/Deny action buttons.

    Each button's ``action_id`` encodes the decision; its ``value`` carries the
    callback id so the block_actions parser can route the tap back to the run.
    """
    text_parts = [f"*Approval needed:* {_slack_escape(plan.title)}"]
    if plan.reason:
        text_parts.append(_slack_escape(plan.reason))
    if plan.action_summary:
        text_parts.append(f"> Action: `{_slack_escape(plan.action_summary)}`")
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_slack_text("\n".join(text_parts), 2900),
            },
        }
    ]
    elements: list[dict[str, Any]] = []
    for button in plan.buttons:
        action_id = SLACK_APPROVAL_ACTION_ID_BY_DECISION.get(button.decision)
        if action_id is None:
            continue
        element: dict[str, Any] = {
            "type": "button",
            "action_id": action_id,
            "text": {
                "type": "plain_text",
                "text": _truncate_slack_text(button.label, 74) or "Approve",
            },
            "value": plan.callback_id,
        }
        if button.style in ("primary", "danger"):
            element["style"] = button.style
        elements.append(element)
    blocks.append({"type": "actions", "elements": elements})
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
