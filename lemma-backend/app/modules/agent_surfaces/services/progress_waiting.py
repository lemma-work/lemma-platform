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

logger = get_logger(__name__)


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

        The run pauses with a WAITING event before terminating. The narration
        that led up to the question travels *with* it rather than ahead of it:
        two sends arrive as two messages on a chat surface, and as two emails on
        a surface that only gets one. Delivering it here also marks the final
        answer delivered, so ``on_run_finished`` does not re-send it.

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
        await self._render_waiting_prompt(
            kind=str(kind),
            conversation=conversation,
            tool_call_id=tool_call_id,
            rendered_key=rendered_key,
            narration=self._take_pre_question_narration(),
        )

        if not ends_run:
            # Nothing about this run's final answer is settled yet: the narration
            # above was the lead-in to the prompt, and the real answer only comes
            # after the decision. Unlatch delivery so on_run_finished still sends
            # it, and drop what was already delivered so it is not repeated.
            self._final_delivered = False
            self._final_answer_text = None
            self._buffered_text = None

    def _take_pre_question_narration(self) -> str | None:
        """The buffered lead-in to the question, claimed exactly once.

        Claiming it is what stops ``on_run_finished`` sending the same text
        again as a separate answer.
        """
        if self._final_delivered:
            return None
        self._final_delivered = True
        if self._run_errored:
            return None
        return (self._final_answer_text or self._buffered_text or "").strip() or None

    async def _render_waiting_prompt(
        self,
        *,
        kind: str,
        conversation: Conversation,
        tool_call_id: str,
        rendered_key: tuple[str, str] | None,
        narration: str | None = None,
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
                        narration=narration,
                    )
                else:
                    delivered = await service.send_approval_prompt_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                        narration=narration,
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
