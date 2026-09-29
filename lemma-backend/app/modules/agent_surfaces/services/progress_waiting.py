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
from app.modules.agent_surfaces.services.pending_envelope import RunFiles
from app.modules.agent_surfaces.services.progress_events import _surface_platform

logger = get_logger(__name__)


class ProgressWaitingMixin:
    """Split out of :class:`SurfaceAgentRunProgressObserver`; see the module docstring."""

    #: Whose held files the prompts it sends carry; set by the observer.
    _run_files: RunFiles

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
        if kind not in ("ask_user", "request_approval", "browser_sign_in"):
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
        """Send the questions or the approval prompt; on failure, ask in words.

        A stuck WAITING run must never be silent -- this is the swallow class
        that hid the ask_user bug. A prompt that does not arrive is asked again
        as plain text, and only if that fails too does it give its rendered-key
        back so a later WAITING event for the same tool call is not deduped away.
        """
        async with self.uow_factory() as uow:
            service = self.egress_factory(uow)
            try:
                if kind == "ask_user":
                    delivered = await service.send_questions_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                        narration=narration,
                        attach_files_of=self._run_files,
                    )
                elif kind == "browser_sign_in":
                    # A link, not buttons. A sign-in is not a yes/no: the person
                    # has to go somewhere and type something, and rendering it as
                    # an approval would offer them two answers neither of which
                    # is what is being asked.
                    delivered = await service.send_sign_in_prompt_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                        narration=narration,
                    )
                else:
                    delivered = await service.send_approval_prompt_for_conversation(
                        conversation_id=conversation.id,
                        tool_call_id=tool_call_id or None,
                        narration=narration,
                        attach_files_of=self._run_files,
                    )
            except Exception:
                # Said out loud: this used to give the key back and log nothing,
                # so a prompt that raised left a run parked on a question nobody
                # was shown, with no trace of why.
                logger.warning(
                    "agent_surfaces.progress_observer.waiting_prompt_failed.degraded",
                    conversation_id=str(conversation.id),
                    kind=kind,
                    exc_info=True,
                )
                delivered = False
            if delivered:
                return
            if _surface_platform(conversation) is None:
                # Not a surface conversation -- somebody is in the web app, where
                # the pause renders itself. Nothing to say and nobody to say it
                # to; the key goes back so the event is not treated as handled.
                self._allow_waiting_retry(rendered_key)
                return
            # A person on a chat surface is now waited on for an answer that
            # cannot come: the run is parked, and nothing re-emits the WAITING
            # event for a later attempt to use the key we give back. So ask again
            # in plain words -- a message is far likelier to land than the
            # control that just failed -- and only if even that fails does the
            # key go back.
            logger.warning(
                "agent_surfaces.progress_observer.waiting_prompt_not_delivered.degraded",
                conversation_id=str(conversation.id),
                kind=kind,
                tool_call_id=tool_call_id,
            )
            if await self._ask_in_plain_words(
                service, conversation, kind, tool_call_id
            ):
                return
            self._allow_waiting_retry(rendered_key)

    async def _ask_in_plain_words(
        self, service, conversation: Conversation, kind: str, tool_call_id: str
    ) -> bool:
        """Last resort for a prompt whose native form did not arrive."""
        try:
            return await service.send_prompt_as_text_for_conversation(
                conversation_id=conversation.id,
                kind=kind,
                tool_call_id=tool_call_id or None,
            )
        except Exception:
            logger.warning(
                "agent_surfaces.progress_observer.waiting_prompt_fallback_failed.degraded",
                conversation_id=str(conversation.id),
                kind=kind,
                exc_info=True,
            )
            return False

    def _allow_waiting_retry(self, rendered_key: tuple[str, str] | None) -> None:
        """Let a later WAITING event for this tool call render again."""
        if rendered_key is not None:
            self._rendered_waiting_tool_calls.discard(rendered_key)
