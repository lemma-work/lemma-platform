from __future__ import annotations

from app.modules.agent_surfaces.domain.models import ProgressHandle, ProgressHandleKind

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

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
from app.modules.agent.contracts import ToolContext
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.rendering import (
    ThinkingStreamFilter,
)
from app.modules.agent_surfaces.services.contact_stream_gate import (
    ContactGate,
    contact_refuses_answers,
)
from app.modules.agent_surfaces.services.egress_service import SurfaceEgress
from app.modules.agent_surfaces.services.live_display import forget_live_display
from app.modules.agent_surfaces.services.progress_display import (
    ProgressDisplayMixin,
)
from app.modules.agent_surfaces.services.pending_envelope import (
    RunFiles,
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
def _delivery_errors() -> tuple[type[BaseException], ...]:
    return (SQLAlchemyError, *platform_transport_errors())


# Read from the capability registry once; a mapping so a test can shorten one.
_TYPING_REFRESH_INTERVAL_SECONDS = {
    platform: capabilities.typing_refresh_seconds
    for platform, capabilities in PLATFORM_CAPABILITIES.items()
    if capabilities.typing_refresh_seconds is not None
}
_MAX_TYPING_REFRESH_SECONDS = 15 * 60.0


# Email recipients get one composed reply, not a stream of chat messages: the
# run's delivery row. Which platforms count as email is decided by `is_email`
# in the capability registry.
class SurfaceAgentRunProgressObserver(
    ProgressWaitingMixin, ProgressDisplayMixin, TokenStreamMixin
):
    """Reflect agent run progress through platform-native surface indicators.

    Display only. The agent's final answer (or its failure notice) is the
    run's channel line and its delivery, written once the run has finished
    (``surface.reply_for_run``) and sent by the channel delivery executor --
    never by this object, so a worker dying mid-run loses a progress bubble
    and not an answer. A contact whose rules refuse the agent's answer now
    (they opted out, or WhatsApp's window closed) is shown nothing at all. Its
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
        contact_gate: ContactGate | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self.egress_factory = egress_factory
        #: Whether a conversation's contact refuses its answer now; see
        #: ``contact_stream_gate``.
        self.contact_gate: ContactGate = contact_gate or (
            lambda conversation_id: contact_refuses_answers(
                uow_factory, conversation_id
            )
        )
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
        # Set once a pause's card carried the buffered text (``progress_waiting``).
        self._final_delivered = False
        # Whose held files a pause's card carries; set from the run's context,
        # which is the only place the run's id is known.
        self._run_files = RunFiles(None)
        self._run_errored = False
        self._run_error_text: str | None = None
        # Opaque handle for the live progress message on streaming platforms
        # (Telegram/Teams), threaded across edits and cleared on finish.
        self._progress_handle: ProgressHandle | None = None
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
        # Set when an answer sanitized away to nothing. Display only: the
        # reply itself is rendered from the run (``delivery_sender``).
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
        # A run started by a private note in Lemma: nothing of it goes to the
        # platform -- no stream, no typing, no answer. See `domain/private_notes`
        # in the agent module; the run's context says so from its first event.
        self._stays_in_lemma = False

    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ToolContext,
    ) -> None:
        self._run_files = RunFiles(ctx.agent_run_id)
        self._run_started_at = time.monotonic()
        self._stays_in_lemma = not getattr(ctx, "delivers_to_surface", True)
        platform = _surface_platform(conversation)
        if platform is None or self._stays_in_lemma:
            return
        if conversation.contact_id is not None and await self.contact_gate(
            conversation.id
        ):
            # Their answer will not go; neither does anything leading up to it.
            self._stays_in_lemma = True
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
        ctx: ToolContext,
    ) -> None:
        self._run_files = RunFiles(ctx.agent_run_id)
        if self._stays_in_lemma:
            return
        if event.type in {AgentEventType.ERROR, AgentEventType.REJECTED}:
            self._run_errored = True
            self._run_error_text = _safe_run_error_text(
                event, _surface_platform(conversation)
            )
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

    async def _clear_progress(self, conversation_id) -> None:
        if not self._progress_handle:
            return
        handle = self._progress_handle
        self._progress_handle = None
        run_id = self._run_files.agent_run_id
        if run_id is not None and handle.kind is ProgressHandleKind.STREAM:
            # Taken down here, so the reply must not try to close it.
            await forget_live_display(run_id)
        try:
            async with self.uow_factory() as uow:
                service = self.egress_factory(uow)
                await service.progress.clear_progress(
                    conversation_id=conversation_id,
                    progress_handle=handle,
                )
        except _delivery_errors():
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
        ctx: ToolContext,
    ) -> None:
        """Finish the run's display; the answer is not the observer's to send.

        The finished run's answer is its channel line and delivery
        (``surface.reply_for_run``), sent by the channel delivery executor --
        so a worker that dies here loses a typing bubble, never the answer.
        What is left for the observer is display:

        * the typing refresh stops;
        * a live Slack stream carrying an answer is flushed to the last token
          and *left open*, sealed in its ``live_display`` record: the reply
          closes this same message with the rest of the answer, so the thread
          holds one message, as it did when the observer sent it;
        * any other live message (a progress line, a stream of a run with no
          answer to close it with) is closed and removed, and the reply, if
          there is one, arrives as the one message in the thread.
        """
        self._run_files = RunFiles(ctx.agent_run_id)
        if self._stays_in_lemma:
            return
        await self._stop_typing()
        if await self._hand_stream_to_reply(conversation):
            return
        await self._clear_progress(conversation.id)

    async def _hand_stream_to_reply(self, conversation: Conversation) -> bool:
        """Leave a live stream for the run's reply to close; True if it was left."""
        run_id = self._run_files.agent_run_id
        handle = self._progress_handle
        if handle is None or run_id is None or self._run_errored:
            return False
        if handle.kind is not ProgressHandleKind.STREAM:
            return False
        capabilities = PLATFORM_CAPABILITIES.get(_surface_platform(conversation) or "")
        if capabilities is None or not capabilities.finishes_stream_with_answer:
            return False
        if not await self._reply_will_close(run_id):
            return False
        try:
            await self._flush_tokens(conversation, final=True)
        except _delivery_errors():
            # The reply closes the stream with whatever it does not show yet,
            # so a last flush that failed costs nothing but a later chunk.
            logger.warning(
                "agent_surfaces.progress_observer.final_flush_failed.degraded",
                conversation_id=str(conversation.id),
                exc_info=True,
            )
        await self._record_stream(conversation, sealed=True)
        self._progress_handle = None
        return True

    async def _reply_will_close(self, run_id: UUID) -> bool:
        """Whether this run's reply is an answer the stream can carry.

        The same test ``surface.reply_for_run`` applies: a completed run, not
        private, that wrote an answer. Anything else -- a stop, a pause, a
        failure notice -- leaves no reply to close the stream, so it comes
        down now rather than spinning beside whatever follows.
        """
        from app.modules.agent.contracts.run_replies import run_reply_material

        try:
            async with self.uow_factory() as uow:
                material = await run_reply_material(uow, run_id)
        except SQLAlchemyError:
            logger.warning(
                "agent_surfaces.progress_observer.reply_material_unread.degraded",
                run_id=str(run_id),
                exc_info=True,
            )
            return False
        return (
            material is not None
            and material.status == "COMPLETED"
            and not material.private
            and bool(material.answer)
        )

    async def on_run_failed(
        self,
        conversation: Conversation,
        error: Exception,
    ) -> None:
        """A run that died: its display comes down; the notice is the run's reply.

        The finalize records the failure and writes the failure notice as the
        run's delivery, like any other answer.
        """
        del error
        if self._stays_in_lemma:
            return
        await self._stop_display(conversation.id)

    async def _stop_display(self, conversation_id: UUID) -> None:
        await self._stop_typing()
        await self._clear_progress(conversation_id)

    async def _stop_typing(self) -> None:
        task = self._typing_task
        self._typing_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected after task.cancel(); the cleanup must continue.
                pass

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
