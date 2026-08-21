"""Rendering a paused run -- an ask_user question or an approval -- on a surface.

The run stops with a WAITING event and everything here is about what the person
sees while it is stopped: the narration that led up to the question, the prompt
itself, and making sure a prompt that reached nobody can be tried again rather
than leaving the run silently stuck.
"""

from __future__ import annotations


from app.core.log.log import get_logger
from app.modules.agent.contracts import Conversation
from app.modules.agent.contracts import (
    AgentEvent,
)
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
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
# Token flush policy: batch deltas so a fast model does not spend the Slack
# rate limit one word at a time, while staying frequent enough to read as live.
_TOKEN_FLUSH_CHARS = 280
_TOKEN_FLUSH_INTERVAL_SECONDS = 0.8
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


class ProgressWaitingMixin:
    """Split out of :class:`SurfaceAgentRunProgressObserver`; see the module docstring."""

    async def _handle_waiting_event(
        self,
        event: AgentEvent,
        conversation: Conversation,
        *,
        ends_run: bool = True,
    ) -> None:
        """Render a paused ``ask_user`` or ``request_approval`` on the surface.

        The run pauses with a WAITING event before terminating. We deliver any
        buffered narration first (so the lead-in to the question still reaches the
        user), mark the final answer delivered so ``on_run_finished`` doesn't
        re-send it, then render the questions / approval prompt.

        ``ends_run`` is False for an Agent Host permission pause, which happens
        inside a run that keeps going; see the reset at the end.
        """
        data = event.data if isinstance(event.data, dict) else {}
        kind = data.get("kind")
        if kind not in ("ask_user", "request_approval"):
            return
        tool_call_id = str(data.get("tool_call_id") or "")
        rendered_key: tuple[str, str] | None = None
        if tool_call_id:
            rendered_key = (str(kind), tool_call_id)
            if rendered_key in self._rendered_waiting_tool_calls:
                return
            self._rendered_waiting_tool_calls.add(rendered_key)

        await self._clear_progress(conversation.id)
        await self._deliver_pre_question_narration(conversation)
        await self._render_waiting_prompt(
            kind=str(kind),
            conversation=conversation,
            tool_call_id=tool_call_id,
            rendered_key=rendered_key,
        )

        if not ends_run:
            # Nothing about this run's final answer is settled yet: the narration
            # above was the lead-in to the prompt, and the real answer only comes
            # after the decision. Unlatch delivery so on_run_finished still sends
            # it, and drop what was already delivered so it is not repeated.
            self._final_delivered = False
            self._final_answer_text = None
            self._buffered_text = None

    async def _deliver_pre_question_narration(self, conversation: Conversation) -> None:
        """Deliver buffered narration -- the lead-in to the question -- exactly once."""
        if self._final_delivered:
            return
        self._final_delivered = True
        if self._run_errored:
            return
        message = (self._final_answer_text or self._buffered_text or "").strip()
        if not message:
            return
        try:
            await self._send_agent_message(
                conversation_id=conversation.id,
                message=message,
            )
        except Exception:
            logger.debug(
                "agent_surfaces.progress_observer.surface_pre_question_narration_conversation.diagnostic"
            )

    async def _render_waiting_prompt(
        self,
        *,
        kind: str,
        conversation: Conversation,
        tool_call_id: str,
        rendered_key: tuple[str, str] | None,
    ) -> None:
        """Send the questions or the approval prompt, and un-dedupe on failure.

        A stuck WAITING run must never be silent -- this is the swallow class
        that hid the ask_user bug -- so anything that does not reach the user
        gives its rendered-key back, letting a later WAITING event for the same
        tool call try again instead of being deduped away.
        """
        async with self.uow_factory() as uow:
            service = self.service_factory(uow)
            try:
                if kind == "ask_user":
                    delivered = await service.send_questions_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                    )
                else:
                    delivered = await service.send_approval_prompt_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                    )
                if not delivered:
                    # Nothing reached the user; the send method logs the precise
                    # reason, so this only has to say that it happened.
                    self._allow_waiting_retry(rendered_key)
                    logger.debug(
                        "agent_surfaces.progress_observer.surface_s_waiting_but_nothing.diagnostic",
                        tool_call_id=tool_call_id,
                    )
            except Exception:
                self._allow_waiting_retry(rendered_key)

    def _allow_waiting_retry(self, rendered_key: tuple[str, str] | None) -> None:
        """Let a later WAITING event for this tool call render again."""
        if rendered_key is not None:
            self._rendered_waiting_tool_calls.discard(rendered_key)
