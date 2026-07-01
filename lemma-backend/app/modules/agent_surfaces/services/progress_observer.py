from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.platforms.rendering import (
    sanitize_user_visible_text,
    strip_thinking_tokens,
)
from app.modules.agent_surfaces.services.ingress_service import (
    AgentSurfaceIngressService,
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
_STREAM_PROGRESS_PLATFORMS = {
    SurfacePlatform.SLACK.value,
    SurfacePlatform.TELEGRAM.value,
    SurfacePlatform.TEAMS.value,
}
_MIN_TEXT_PROGRESS_INTERVAL_SECONDS = 2.0
_MAX_PROGRESS_TEXT_LENGTH = 120
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
_EMAIL_REPLY_TOOL_NAMES = {
    caps.reply_tool for caps in PLATFORM_CAPABILITIES.values() if caps.reply_tool
}


class SurfaceAgentRunProgressObserver:
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
        # Opaque handle for the live progress message on streaming platforms
        # (Telegram/Teams), threaded across edits and cleared on finish.
        self._progress_handle: dict[str, Any] | None = None

    async def on_run_started(
        self,
        conversation: Conversation,
        ctx: ConversationContext,
    ) -> None:
        del ctx
        platform = _surface_platform(conversation)
        interval = _TYPING_REFRESH_INTERVAL_SECONDS.get(platform)
        if interval is None:
            return
        sent = await self._send_indicator(conversation_id=conversation.id)
        if not sent:
            return
        self._typing_task = asyncio.create_task(
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
        if event.type == AgentEventType.ERROR:
            self._run_errored = True
            return

        if event.type == AgentEventType.WAITING:
            await self._handle_waiting_event(event, conversation)
            return

        platform = _surface_platform(conversation)

        # Assistant text is buffered, never sent mid-run, so intermediate
        # narration cannot leak as a separate chat message. The final answer is
        # delivered once on on_run_finished.
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

    async def _handle_waiting_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
    ) -> None:
        """Render a paused ``ask_user`` or ``request_approval`` on the surface.

        The run pauses with a WAITING event before terminating. We deliver any
        buffered narration first (so the lead-in to the question still reaches the
        user), mark the final answer delivered so ``on_run_finished`` doesn't
        re-send it, then render the questions / approval prompt.
        """
        data = event.data if isinstance(event.data, dict) else {}
        kind = data.get("kind")
        if kind not in ("ask_user", "request_approval"):
            return
        await self._clear_progress(conversation.id)
        # Deliver buffered narration (the lead-in to the question) exactly once.
        if not self._final_delivered:
            self._final_delivered = True
            if not self._run_errored:
                message = (
                    self._final_answer_text or self._buffered_text or ""
                ).strip()
                if message:
                    try:
                        await self._send_agent_message(
                            conversation_id=conversation.id,
                            message=message,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Surface pre-question narration failed "
                            "conversation=%s error=%s",
                            conversation.id,
                            exc,
                        )
        tool_call_id = data.get("tool_call_id")
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            try:
                if kind == "ask_user":
                    await service.send_questions_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=str(tool_call_id) if tool_call_id else None,
                    )
                else:
                    await service.send_approval_prompt_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=str(tool_call_id) if tool_call_id else None,
                    )
            except Exception as exc:
                logger.warning(
                    "Surface %s render failed conversation=%s error=%s",
                    kind,
                    conversation.id,
                    exc,
                )

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
                pass
        await self._clear_progress(conversation.id)
        await self._deliver_final_answer(conversation)

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
            return
        platform = _surface_platform(conversation)
        if platform in _EMAIL_PLATFORMS and self._email_reply_tool_called:
            return
        message = (self._final_answer_text or self._buffered_text or "").strip()
        if not message:
            return
        try:
            await self._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception as exc:
            logger.warning(
                "Surface final answer delivery failed conversation=%s error=%s",
                conversation.id,
                exc,
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
        except Exception as exc:
            logger.warning(
                "Surface progress typing loop stopped conversation=%s error=%s",
                conversation_id,
                exc,
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


def _surface_platform(conversation: Conversation) -> str | None:
    metadata = conversation.metadata or {}
    platform = metadata.get("surface_platform") if isinstance(metadata, dict) else None
    return str(platform).upper() if platform else None


def _email_reply_tool_called(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    return (
        isinstance(data, MessageDraft)
        and data.kind == MessageKind.TOOL_CALL
        and data.tool_name in _EMAIL_REPLY_TOOL_NAMES
    )


def _progress_text_from_event(event: AgentEvent) -> str | None:
    """Derive a short progress status from thinking/tool activity.

    Tool calls prefer an explicit ``comment`` in the tool args, falling back to
    the tool name; thinking events surface a generic "Thinking…" status.
    """
    if event.type != AgentEventType.MESSAGE:
        return None
    data = event.data
    if not isinstance(data, MessageDraft):
        return None
    if data.kind == MessageKind.TOOL_CALL:
        comment = _find_comment(data.tool_args)
        if comment:
            # A comment that is entirely reasoning sanitizes to empty -> no
            # progress update (rather than streaming a blank/leaky message).
            return _sanitize_progress_text(comment) or None
        if data.tool_name:
            return _sanitize_progress_text(f"Using {data.tool_name}")
        return None
    if data.kind == MessageKind.THINKING:
        return "Thinking…"
    return None


def _is_final_answer_event(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    if not isinstance(data, MessageDraft):
        return False
    metadata = data.metadata or {}
    return metadata.get("is_final_answer") is True


def _is_tool_activity_event(event: AgentEvent) -> bool:
    if event.type != AgentEventType.MESSAGE:
        return False
    data = event.data
    return isinstance(data, MessageDraft) and data.kind in (
        MessageKind.TOOL_CALL,
        MessageKind.TOOL_RETURN,
    )


def _join_text(existing: str | None, new: str) -> str:
    if not existing:
        return new
    return f"{existing}\n\n{new}"


def _assistant_text_from_event(event: AgentEvent) -> str | None:
    if event.type != AgentEventType.MESSAGE:
        return None
    data = event.data
    if not isinstance(data, MessageDraft):
        return None
    role = data.role.value if isinstance(data.role, MessageRole) else str(data.role)
    if role != MessageRole.ASSISTANT.value:
        return None
    if data.kind != MessageKind.TEXT:
        return None
    # Strip inline reasoning/thinking tags (e.g. ``…``) that some
    # OpenAI-compatible models emit inside the text content. Without this the
    # thinking block would be buffered as assistant text and delivered to the
    # surface as a normal message.
    text = strip_thinking_tokens(data.text or "")
    return text or None


def _find_comment(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("comment", "progress_comment", "progress", "status"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    request = value.get("request")
    if isinstance(request, dict):
        return _find_comment(request)
    return None


def _sanitize_progress_text(value: str) -> str:
    # Strip model reasoning BEFORE collapsing/truncating: a model that writes
    # ``<think>…</think>`` into a tool-call comment must never have it streamed
    # to the surface as a live progress update.
    text = " ".join(sanitize_user_visible_text(value).split())
    if len(text) <= _MAX_PROGRESS_TEXT_LENGTH:
        return text
    return text[: _MAX_PROGRESS_TEXT_LENGTH - 1].rstrip() + "..."
