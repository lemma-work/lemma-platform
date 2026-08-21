from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.progress_waiting import (
    ProgressWaitingMixin,
)
from app.core.log.log import get_logger
from app.core.request_context import create_inherited_task
from app.modules.agent.contracts import Conversation
from app.modules.agent.contracts import (
    AgentEvent,
    AgentEventType,
)
from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.platforms.rendering import (
    ThinkingStreamFilter,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
)
from app.modules.agent_surfaces.services.token_stream import TokenStreamMixin
from app.modules.agent_surfaces.services.progress_events import (
    _assistant_text_from_event,
    _assistant_text_was_all_reasoning,
    _email_reply_tool_called,
    _is_agent_host_permission_event,
    _is_final_answer_event,
    _is_tool_activity_event,
    _join_text,
    _progress_text_from_event,
    _safe_run_error_text,
    _surface_platform,
)

logger = get_logger(__name__)

_TYPING_REFRESH_INTERVAL_SECONDS = {
    SurfacePlatform.TELEGRAM.value: 4.0,
    SurfacePlatform.TEAMS.value: 10.0,
}
_MAX_TYPING_REFRESH_SECONDS = 15 * 60.0
# Slack/Telegram/Teams render progress as a live, edited message (streaming):
# Slack via chat.update, Telegram via editMessageText, Teams via PUT activity.
# WhatsApp has no message-edit API, so it gets no per-step progress (the inbound
# reaction indicator signals work) and email gets a single composed reply.
_TEXT_PROGRESS_PLATFORMS: set[str] = set()
# Slack is deliberately absent: it streams the answer token by token, and a
# step chunk appended into that same stream lands *inside* the sentence being
# written — splitting it mid-word. The streamed text is the progress indicator,
# so a separate step timeline is both redundant and destructive.
_STREAM_PROGRESS_PLATFORMS = {
    SurfacePlatform.TELEGRAM.value,
    SurfacePlatform.TEAMS.value,
}
_MIN_TEXT_PROGRESS_INTERVAL_SECONDS = 2.0
# Email recipients should get one composed reply, not a stream of chat
# messages. Agents reply via the platform reply tools; the observer only
# falls back to emailing the final assistant text if no reply was sent.
#
# Derived from the platform-capability registry (not hand-maintained) so a
# newly added email platform is automatically covered here too — a hardcoded
# set previously let Resend fall through both checks even after it shipped as
# a full `is_email=True` platform, causing a duplicate auto-echoed send via
# broken fallback credentials on every real Resend reply.
_EMAIL_PLATFORMS = {
    caps.platform for caps in PLATFORM_CAPABILITIES.values() if caps.is_email
}


class SurfaceAgentRunProgressObserver(ProgressWaitingMixin, TokenStreamMixin):
    """Reflect agent run progress through platform-native surface indicators.

    A surface conversation should receive exactly one content message per run:
    the agent's final answer. The agent's intermediate narration, reasoning
    (``ThinkingContent``) and tool activity (``ToolCallContent`` /
    ``ToolReturnContent``) must never be delivered as chat messages — they only
    drive progress indicators (typing for Telegram/Teams, a status string for
    Slack). To achieve this the observer buffers assistant text during the run
    and delivers the final answer once on ``on_run_finished``, resetting the
    buffer whenever a tool runs so only the post-final-tool text survives.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        service_factory: Callable[[SqlAlchemyUnitOfWork], AgentSurfaceIngressService],
    ) -> None:
        self.uow_factory = uow_factory
        self.service_factory = service_factory
        self._typing_task: asyncio.Task[None] | None = None
        self._last_text_progress_at = 0.0
        self._last_text_progress: str | None = None
        # Assistant text that explicitly carried ``is_final_answer`` (structured
        # agents). Takes precedence over the heuristic buffer below.
        self._final_answer_text: str | None = None
        # Last contiguous block of assistant text; reset when a tool runs so
        # pre-tool narration is discarded and only the final answer remains.
        self._buffered_text: str | None = None
        self._reset_text_on_next = False
        self._final_delivered = False
        # Set when the agent calls an email reply tool. Display resources are
        # delivered by the display_resource tool itself (chat) or shared via the
        # email reply tool's attachments (email), so the observer no longer
        # handles display_resource at all — it only buffers text + progress.
        self._email_reply_tool_called = False
        self._run_errored = False
        self._run_error_text: str | None = None
        self._error_delivered = False
        # Opaque handle for the live progress message on streaming platforms
        # (Telegram/Teams), threaded across edits and cleared on finish.
        self._progress_handle: dict[str, Any] | None = None
        # Live token streaming. ``_streamed_text`` is what the user has already
        # seen in the stream; ``_token_buffer`` is what has arrived but not yet
        # been flushed. Flushing every token would blow Slack's rate limit, so
        # deltas are batched by size or elapsed time, whichever comes first.
        self._token_buffer: str = ""
        self._streamed_text: str = ""
        self._last_token_flush: float = 0.0
        # Reasoning arrives inline in the text stream on some models
        # (``<think>…</think>``), and a tag can straddle two deltas — so the
        # filter has to be stateful across the whole run.
        self._think_filter = ThinkingStreamFilter()
        # Set when an answer sanitized away to nothing, which is the one case
        # where "no text to deliver" means an answer was lost rather than never
        # written. See ``_deliver_final_answer``.
        self._answer_was_all_reasoning = False
        self._rendered_waiting_tool_calls: set[tuple[str, str]] = set()

    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        del ctx
        platform = _surface_platform(conversation)
        if platform is None:
            return
        capabilities = PLATFORM_CAPABILITIES.get(platform or "")
        if capabilities is not None and capabilities.finishes_stream_with_answer:
            # Open the stream up front. Slack shows a live indicator on an open
            # stream, which is the only "working on it" signal a *channel* gets
            # — setStatus is assistant-DM only, and waiting for the first token
            # leaves a tool-heavy run looking dead. An empty stream is disposed
            # of by end_progress if no answer ever arrives.
            await self._open_stream(conversation)
        interval = _TYPING_REFRESH_INTERVAL_SECONDS.get(platform)
        if interval is None:
            return
        sent = await self._send_indicator(conversation_id=conversation.id)
        if not sent:
            return
        self._typing_task = create_inherited_task(
            self._refresh_typing_loop(
                conversation_id=conversation.id,
                interval=interval,
            )
        )

    async def on_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        del ctx
        if event.type in {AgentEventType.ERROR, AgentEventType.REJECTED}:
            self._run_errored = True
            self._run_error_text = _safe_run_error_text(event)
            return

        if event.type == AgentEventType.WAITING:
            await self._handle_waiting_event(event, conversation)
            return

        if event.type == AgentEventType.TOKEN:
            await self._on_token(event, conversation)
            return

        if _is_agent_host_permission_event(event):
            # An Agent Host pauses for permission *mid-run*: render the prompt
            # like any other approval, but leave the run's delivery state alone
            # so the answer that follows still arrives as its final message.
            await self._handle_waiting_event(event, conversation, ends_run=False)
            return

        platform = _surface_platform(conversation)

        # Assistant text is buffered, never sent mid-run, so intermediate
        # narration cannot leak as a separate chat message. The final answer is
        # delivered once on on_run_finished.
        if _assistant_text_was_all_reasoning(event):
            # The model wrote reasoning and stopped. Stripping it is right, but
            # it leaves nothing to send, and a turn that ends in silence reads
            # as the agent ignoring the person. Remembered so delivery can say
            # so instead.
            self._answer_was_all_reasoning = True
        assistant_text = _assistant_text_from_event(event)
        if assistant_text is not None:
            if _is_final_answer_event(event):
                self._final_answer_text = assistant_text
            elif self._reset_text_on_next:
                self._buffered_text = assistant_text
                self._reset_text_on_next = False
            else:
                self._buffered_text = _join_text(self._buffered_text, assistant_text)
            return

        # display_resource is delivered by the tool (chat) or shared via the email
        # reply tool's attachments (email); the observer no longer routes it.
        # Thinking / tool-call / tool-return content is never a content message.
        # A tool run means any buffered text was intermediate narration, so the
        # next assistant text starts a fresh (final) answer block.
        if _is_tool_activity_event(event):
            self._reset_text_on_next = True
            if _email_reply_tool_called(event):
                self._email_reply_tool_called = True

        await self._maybe_send_text_progress(event, platform, conversation.id)

    async def _maybe_send_text_progress(
        self,
        event: AgentEvent,
        platform: str | None,
        conversation_id,
    ) -> None:
        """Reflect thinking/tool activity as a status string where supported.

        Telegram/Teams show a typing indicator (refreshed by the loop started in
        on_run_started); Slack is the only platform that renders a status text.
        """
        streams = platform in _STREAM_PROGRESS_PLATFORMS
        if platform not in _TEXT_PROGRESS_PLATFORMS and not streams:
            return
        progress_text = _progress_text_from_event(event)
        if not progress_text:
            return
        now = time.monotonic()
        if (
            progress_text == self._last_text_progress
            or now - self._last_text_progress_at < _MIN_TEXT_PROGRESS_INTERVAL_SECONDS
        ):
            return
        self._last_text_progress = progress_text
        self._last_text_progress_at = now
        if streams:
            await self._stream_progress(conversation_id, progress_text)
        else:
            await self._send_indicator(
                conversation_id=conversation_id,
                metadata={"progress_text": progress_text},
            )

    async def _stream_progress(self, conversation_id, progress_text: str) -> None:
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            handle = await service.send_progress_update_for_conversation(
                conversation_id=conversation_id,
                progress_text=progress_text,
                progress_handle=self._progress_handle,
            )
        if handle is not None:
            self._progress_handle = handle

    async def _finish_stream_with_answer(self, conversation: Conversation) -> bool:
        """Close a live stream with the final answer, so they are one message.

        Only attempted on platforms whose streaming API can carry the answer
        (``finishes_stream_with_answer``) and only when there is a live stream
        and an answer to put in it. Returns True when the answer was delivered
        this way, which also marks it delivered so ``_deliver_final_answer``
        does not send it a second time.
        """
        if self._progress_handle is None or self._final_delivered or self._run_errored:
            return False
        capabilities = PLATFORM_CAPABILITIES.get(_surface_platform(conversation) or "")
        if capabilities is None or not capabilities.finishes_stream_with_answer:
            return False
        await self._flush_tokens(conversation, final=True)
        message = (self._final_answer_text or self._buffered_text or "").strip()
        if not message:
            return False
        # Whatever already streamed is on screen. Send only what is left, or the
        # user reads the answer twice.
        if self._streamed_text:
            if message.startswith(self._streamed_text):
                message = message[len(self._streamed_text) :]
            else:
                # The stream and the final text disagree (a retry, or a rewritten
                # answer). Trust the stream the user already saw and just close.
                message = ""
        handle = self._progress_handle
        try:
            async with self.uow_factory() as uow:
                service = self.service_factory(uow)
                delivered = await service.finish_progress_for_conversation(
                    conversation_id=conversation.id,
                    progress_handle=handle,
                    message=message,
                    already_streamed=bool(self._streamed_text),
                )
        except SQLAlchemyError:
            logger.debug(
                "agent_surfaces.progress_observer.surface_finish_stream_conversation.diagnostic"
            )
            return False
        if not delivered:
            return False
        self._progress_handle = None
        self._final_delivered = True
        return True

    async def _clear_progress(self, conversation_id) -> None:
        if not self._progress_handle:
            return
        handle = self._progress_handle
        self._progress_handle = None
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            await service.clear_progress_for_conversation(
                conversation_id=conversation_id,
                progress_handle=handle,
            )

    async def on_run_finished(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        del ctx
        task = self._typing_task
        self._typing_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected after task.cancel(); delivery cleanup must continue.
                pass
        if not await self._finish_stream_with_answer(conversation):
            await self._clear_progress(conversation.id)
        await self._deliver_final_answer(conversation)

    async def on_run_failed(
        self,
        conversation: Conversation,
        error: Exception,
    ) -> None:
        """Deliver failures raised before the harness can emit an error event."""
        del error
        self._run_errored = True
        self._run_error_text = (
            "I couldn’t finish that request. "
            "Try it again without resending your message."
        )
        task = self._typing_task
        self._typing_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._clear_progress(conversation.id)
        await self._deliver_run_error(conversation)

    async def _deliver_final_answer(self, conversation: Conversation) -> None:
        """Deliver the single final answer once the run has finished.

        Email surfaces only fall back to sending the buffered text when the
        agent did not already reply via a reply tool. Chat surfaces always send
        the final buffered answer. Nothing is sent if the run errored or there
        is no usable text.
        """
        if self._final_delivered:
            return
        self._final_delivered = True
        if self._run_errored:
            await self._deliver_run_error(conversation)
            return
        platform = _surface_platform(conversation)
        if platform in _EMAIL_PLATFORMS and self._email_reply_tool_called:
            return
        message = (self._final_answer_text or self._buffered_text or "").strip()
        if not message and self._answer_was_all_reasoning:
            # An answer existed and sanitizing removed all of it, so the run
            # "succeeded" with nothing to show. Saying nothing is the one
            # outcome the person cannot act on — they cannot tell it from the
            # agent never having seen the message, and will ask again into the
            # same silence. Say the turn produced nothing instead.
            message = (
                "I thought that through but never wrote the answer. "
                "Ask me again and I'll give it another go."
            )
        if not message:
            return
        try:
            await self._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.progress_observer.surface_final_answer_delivery_conversation.diagnostic"
            )

    async def _deliver_run_error(self, conversation: Conversation) -> None:
        if self._error_delivered:
            return
        if _surface_platform(conversation) in _EMAIL_PLATFORMS:
            self._error_delivered = True
            return
        try:
            await self._send_agent_message(
                conversation_id=conversation.id,
                message=(
                    self._run_error_text
                    or "I couldn’t finish that request. You can try it again."
                ),
                metadata={"retry_action": True},
            )
            self._error_delivered = True
        except Exception:
            logger.debug(
                "agent_surfaces.progress_observer.surface_error_delivery.diagnostic"
            )

    async def _refresh_typing_loop(
        self,
        *,
        conversation_id,
        interval: float,
    ) -> None:
        started_at = time.monotonic()
        try:
            while time.monotonic() - started_at < _MAX_TYPING_REFRESH_SECONDS:
                await asyncio.sleep(interval)
                sent = await self._send_indicator(conversation_id=conversation_id)
                if not sent:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "agent_surfaces.progress_observer.surface_progress_typing_loop_stopped.diagnostic",
                conversation_id=conversation_id,
            )

    async def _send_indicator(
        self,
        *,
        conversation_id,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            return await service.send_processing_indicator_for_conversation(
                conversation_id=conversation_id,
                metadata=metadata,
            )

    async def _send_agent_message(
        self,
        *,
        conversation_id,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            kwargs: dict[str, Any] = {
                "conversation_id": conversation_id,
                "message": message,
            }
            if metadata:
                kwargs["metadata"] = metadata
            return await service.send_agent_message_for_conversation(**kwargs)
