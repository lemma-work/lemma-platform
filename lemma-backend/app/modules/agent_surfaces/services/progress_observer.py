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
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.platforms.rendering import (
    ThinkingStreamFilter,
)
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.progress_display import (
    ProgressDisplayMixin,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
    discard_display_paths,
)
from app.modules.agent_surfaces.services.progress_plan import (
    SurfacePlan,
    plan_from_event,
)
from app.modules.agent_surfaces.services.token_stream import TokenStreamMixin
from app.modules.agent_surfaces.services.progress_events import (
    _assistant_text_from_event,
    _assistant_text_was_all_reasoning,
    _is_agent_host_permission_event,
    _is_final_answer_event,
    _is_tool_activity_event,
    _join_text,
    _safe_run_error_text,
    _surface_platform,
)

logger = get_logger(__name__)

# What a platform call can raise that must not stop the answer being delivered
# another way: the transport family, plus the database work around it.
_DELIVERY_ERRORS: tuple[type[BaseException], ...] = (
    SQLAlchemyError,
    *PLATFORM_TRANSPORT_ERRORS,
)

_TYPING_REFRESH_INTERVAL_SECONDS = {
    SurfacePlatform.TELEGRAM.value: 4.0,
    SurfacePlatform.TEAMS.value: 10.0,
    # WhatsApp couples mark-as-read and the typing bubble into one call, which
    # the ingress path already makes once when it picks the message up. The
    # bubble expires after ~25s, so on anything longer than a quick answer it
    # went dark and stayed dark — dead in exactly the runs where it was the only
    # sign of life. Refreshed inside that window it costs no conversation
    # message and no attention, unlike the posted updates that back it up.
    SurfacePlatform.WHATSAPP.value: 20.0,
}
_MAX_TYPING_REFRESH_SECONDS = 15 * 60.0


# Email recipients get one composed reply, not a stream of chat messages, and
# the observer is the only thing that sends it. Which platforms count as email
# is decided by `is_email` in the capability registry.
class SurfaceAgentRunProgressObserver(
    ProgressWaitingMixin, ProgressDisplayMixin, TokenStreamMixin
):
    """Reflect agent run progress through platform-native surface indicators.

    The agent's final answer is delivered once, on ``on_run_finished``. Its
    intermediate narration, reasoning (``ThinkingContent``) and tool activity
    (``ToolCallContent`` / ``ToolReturnContent``) are never delivered as chat
    messages — they only drive progress indicators. To achieve this the observer
    buffers assistant text during the run and resets the buffer whenever a tool
    runs, so only the post-final-tool text survives. Other things a run sends
    on purpose (a question, an approval, a file, a display resource, a WhatsApp
    progress post) travel their own paths and are not part of this rule.

    What "progress indicator" means is the platform's ``ProgressStyle``: Slack
    streams the answer as it is written, Telegram and Teams keep one live message
    and rewrite it, WhatsApp can only post a new message and so is rationed to a
    plan that has moved, and email shows nothing before the reply. The one thing
    they share is what they show — the agent's ``write_todos`` checklist, which
    is the only account of a long run the person ever gets.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        egress_factory: Callable[[SqlAlchemyUnitOfWork], SurfaceEgress],
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_factory = egress_factory
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
        # The reply was attempted and did not go out. The run's held files stay
        # for the retry of that reply instead of being discarded with the run.
        self._final_send_failed = False
        # Whose held files this observer's replies carry; set from the run's
        # context, which is the only place the run's id is known.
        self._run_files = RunFiles(None)
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
        # The agent's checklist, as of the last ``write_todos`` return, plus the
        # snapshot that was last actually shown. Kept apart so a plan rewritten
        # with the same contents does not spend an update.
        self._plan: SurfacePlan | None = None
        self._shown_plan_signature: tuple[tuple[str, bool], ...] | None = None
        # Rationing for platforms that can only post a *new* message.
        self._run_started_at = time.monotonic()
        self._posted_updates = 0
        self._last_post_at = 0.0
        self._heartbeat_posted = False

    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        self._run_files = RunFiles(ctx.agent_run_id)
        self._run_started_at = time.monotonic()
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
        self._run_files = RunFiles(ctx.agent_run_id)
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

        # display_resource is delivered by the tool (chat) or held for the one
        # reply that carries it (email); the observer no longer routes it.
        # Thinking / tool-call / tool-return content is never a content message.
        # A tool run means any buffered text was intermediate narration, so the
        # next assistant text starts a fresh (final) answer block.
        if _is_tool_activity_event(event):
            self._reset_text_on_next = True
            plan = plan_from_event(event)
            if plan is not None:
                self._plan = plan

        await self._maybe_send_text_progress(event, platform, conversation.id)

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
                service = self.egress_factory(uow)
                delivered = await service.progress.finish_with_answer(
                    conversation_id=conversation.id,
                    progress_handle=handle,
                    message=message,
                    already_streamed=bool(self._streamed_text),
                )
        except _DELIVERY_ERRORS:
            # Not debug: this is the step that turns a live stream into the
            # answer, and a timeout here used to escape `on_run_finished`
            # before `_deliver_final_answer` ran -- the stream was left open and
            # the answer was never sent. Returning False sends the caller down
            # the plain-message path instead.
            logger.warning(
                "agent_surfaces.progress_observer.finish_stream_failed.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
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
        try:
            async with self.uow_factory() as uow:
                service = self.egress_factory(uow)
                await service.progress.clear_progress(
                    conversation_id=conversation_id,
                    progress_handle=handle,
                )
        except _DELIVERY_ERRORS:
            # Clearing is cosmetic and the answer that follows it is not, so a
            # failure here must never be the reason the answer is not sent.
            logger.warning(
                "agent_surfaces.progress_observer.clear_progress_failed.degraded",
                conversation_id=str(conversation_id),
                exc_info=True,
            )

    async def on_run_finished(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        self._run_files = RunFiles(ctx.agent_run_id)
        task = self._typing_task
        self._typing_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected after task.cancel(); delivery cleanup must continue.
                pass
        try:
            finished = await self._finish_stream_with_answer(conversation)
        except _DELIVERY_ERRORS:
            # The flush before the close is outside the guarded call above. Either
            # way the answer still has to go out, and that is the next line.
            logger.warning(
                "agent_surfaces.progress_observer.finish_stream_failed.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            finished = False
        if not finished:
            await self._clear_progress(conversation.id)
        await self._deliver_final_answer(conversation)
        # Anything display_resource held for a single reply belongs to this run,
        # and is released with the reply that carried it. It is discarded here
        # only when nothing is left to recover: a reply that failed to send keeps
        # its files, under this run's key, for the retry of that same reply --
        # no other run reads them, and they expire on their own.
        if not self._final_send_failed:
            await discard_display_paths(conversation.id, self._run_files)

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
        # Same reason as `on_run_finished`: what `display_resource` held belongs
        # to this run. A run that died still held it, and the entry outlived the
        # run -- attaching itself to whatever reply came next, or to nothing at
        # all while the process kept the bytes.
        await discard_display_paths(conversation.id, self._run_files)

    async def _deliver_final_answer(self, conversation: Conversation) -> None:
        """Deliver the single final answer once the run has finished.

        Every surface, the same way: the buffered answer goes out when the run
        stops. Email used to be the exception, deferring to a reply tool the
        agent had to remember to call -- and this path, which ran whenever the
        agent forgot, was never a lesser implementation of it. It threads
        correctly, renders markdown to HTML and keeps the thread's subject,
        which is the whole of what the tool did.

        Nothing is sent if the run errored (the error is delivered instead) or
        if there is no usable text.
        """
        if self._final_delivered:
            return
        if self._run_errored:
            self._final_delivered = True
            await self._deliver_run_error(conversation)
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
            self._final_delivered = True
            return
        # Marked delivered only once it was, not before the send: latching first
        # meant a send that failed still counted as the answer having gone out,
        # and nothing that read the flag afterwards could tell the difference.
        try:
            delivered = await self._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception:
            logger.warning(
                "agent_surfaces.progress_observer.final_answer_not_delivered.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            self._final_send_failed = True
            return
        if delivered:
            self._final_delivered = True
        else:
            self._final_send_failed = True
            # The send path says why -- no surface link, a platform refusal --
            # so this only records that the answer did not go out.
            logger.debug(
                "agent_surfaces.progress_observer.final_answer_unsent.diagnostic",
                conversation_id=str(conversation.id),
            )

    async def _deliver_run_error(self, conversation: Conversation) -> None:
        """Say that the run failed, on every surface including email.

        Email used to be skipped here, so a failed run on an email surface said
        nothing at all -- the person's message simply vanished, and nothing
        distinguished that from never having been read. Now the run stopping is
        the run stopping, whatever stopped it, and the one reply carries the
        failure.
        """
        if self._error_delivered:
            return
        try:
            delivered = await self._send_agent_message(
                conversation_id=conversation.id,
                message=(
                    self._run_error_text
                    or "I couldn’t finish that request. You can try it again."
                ),
                metadata={"retry_action": True},
                carries_files=False,
            )
        except Exception:
            logger.warning(
                "agent_surfaces.progress_observer.run_error_not_delivered.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
            return
        if delivered:
            self._error_delivered = True

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
                # Flagged as a refresh so an adapter can tell a keep-alive from
                # the opening acknowledgement. WhatsApp needs the distinction:
                # its acknowledgement falls back to posting a reaction when the
                # read/typing call is refused, and a fallback that fires on every
                # tick would be an API call every twenty seconds for the whole
                # run.
                sent = await self._send_indicator(
                    conversation_id=conversation_id,
                    metadata={"is_refresh": True},
                )
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
            service = self.egress_factory(uow)
            return await service.progress.show_typing(
                conversation_id=conversation_id,
                metadata=metadata,
            )

    async def _send_agent_message(
        self,
        *,
        conversation_id,
        message: str,
        metadata: dict[str, Any] | None = None,
        carries_files: bool = True,
    ) -> bool:
        async with self.uow_factory() as uow:
            service = self.egress_factory(uow)
            kwargs: dict[str, Any] = {
                "conversation_id": conversation_id,
                "message": message,
            }
            if carries_files:
                # This run's reply, so it is the only one that carries this
                # run's files. A failure notice is not that reply.
                kwargs["attach_files_of"] = self._run_files
            if metadata:
                kwargs["metadata"] = metadata
            return await service.send_agent_message_for_conversation(**kwargs)
