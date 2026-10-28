"""Settling the tool calls a turn's previous attempt left open, before it resumes.

A turn resumed on another worker -- after a crash, an OOM, a lost lease --
reloads its history and carries on from its last committed step. The one gap
is the step in flight: a tool call whose row committed and whose return never
did. Left to the history builder, it became "interrupted, outcome unknown" on
every reload, the model decided whether to repeat it, and a repeat got a *new*
call id, so nothing keyed on the call deduplicated it.

Here the runner settles each open call itself, under the **recorded** call id,
by the tool's replay class (``domain/replay.py``):

* SAFE / KEYED -- run again through the run's tools with that id, so a
  keyed effect returns the first attempt's outcome; the return is persisted.
* UNSAFE -- never run again. The "outcome unknown" return is persisted as a real
  row (so it is not resynthesised on every reload), the model gets a notice
  to check the state before repeating anything, and the person reading the
  conversation gets one line saying which tool may or may not have completed.

A turn whose previous attempt committed its answer and then lost the lease
before its finalize committed is not run again: ``committed_answer`` hands the
runner that answer to finish the run on, so the model is not asked a second
time and the run does not answer twice.

Pausing calls are not touched: an open ``ask_user`` is the conversation waiting
on a person, and the approval machinery owns it. Only in-process (LEMMA) turns
are settled: an Agent Host run keeps its own resume through the host's stream
cursor, and its calls are still the host's to answer.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.services.tool_catalog import ToolSurface, specs_for
from app.modules.agent.services.tool_runtime import ToolRuntime
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.replay import (
    ReplayClass,
    is_replayable_call,
    replay_class,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    to_json_value,
)
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.live import publish_conversation_event
from app.modules.agent.domain.live_events import message_payload
from app.modules.agent.services.run_finalizer import RunFinalizer

if TYPE_CHECKING:
    from app.modules.agent.domain.context import ToolContext
    from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.domain.live_events import message_to_payload
from app.modules.agent.infrastructure.tools.tool_errors import format_tool_error
from app.modules.agent.infrastructure.providers.control_flow import (
    is_control_flow_exception,
)

logger = get_logger(__name__)

#: The return an UNSAFE call gets, stored as a row so a reload does not
#: resynthesise it. Blunt, because the cost of a model reading it as "failed,
#: try again" is the duplicate email or record this class exists to prevent.
OUTCOME_UNKNOWN = (
    "This tool call was interrupted before its result was recorded. It may or "
    "may not have taken effect. Do not repeat it without first checking "
    "whether it already happened -- read the record, file, message or other "
    "state it would have changed -- and repeat it only if it clearly did not."
)
#: What the person reading the conversation is told, once per resumed turn.
INTERRUPTED_NOTICE = (
    "I was interrupted while running {tools}; it may or may not have completed."
)
#: What the model is told once, at the top of the resumed turn.
RESUME_NOTICE = (
    "This turn was interrupted and has resumed on another worker. {count} tool "
    "call(s) were in flight and cannot be safely repeated ({tools}); their "
    "outcome is unknown. Check the current state before repeating any of them."
)


@dataclass(slots=True)
class ReplayReport:
    """What settling a resumed turn's open calls did."""

    unpaired: int = 0
    replayed: int = 0
    unknown: int = 0
    unknown_tools: list[str] = field(default_factory=list)

    @property
    def wrote(self) -> bool:
        return bool(self.replayed or self.unknown)

    def notices(self) -> list[str]:
        if not self.unknown:
            return []
        return [
            RESUME_NOTICE.format(
                count=self.unknown, tools=", ".join(sorted(set(self.unknown_tools)))
            )
        ]


def recorded_args(message: Message) -> dict[str, object]:
    """The call's arguments as recorded: a JSON object, or empty when unreadable."""
    raw = message.tool_args
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class TurnResume:
    """Settles one run's open tool calls."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        message_writer: RunMessageWriter,
    ) -> None:
        self.uow_factory = uow_factory
        self.message_writer = message_writer

    async def settle_for_turn(
        self,
        run_id: UUID,
        *,
        conversation: Conversation,
        agent: Agent,
        ctx: ToolContext,
        messages: list[Message],
        in_process: bool,
        reload: Callable[[], Awaitable[list[Message]]],
    ) -> tuple[list[Message], list[str]]:
        """Settle the open calls, and the history the turn should run on.

        In-process turns only. When anything was written the history is read
        again (``reload``), so the model sees the returns it now has.
        """
        if not in_process:
            return messages, []
        report = await self.settle(
            run_id=run_id, conversation=conversation, agent=agent, ctx=ctx
        )
        if report.wrote:
            messages = await reload()
        return messages, report.notices()

    async def committed_answer(self, run_id: UUID) -> MessageDraft | None:
        """The answer this run already committed as its last step, if it did."""
        async with self.uow_factory() as uow:
            last = await Runs(uow).last_run_message(run_id)
        if last is None or not last.state.is_final_answer:
            return None
        logger.warning("agent.turn.resumed_answered", agent_run_id=str(run_id))
        return MessageDraft(
            role=last.role,
            kind=last.kind,
            text=last.text,
            tool_name=last.tool_name,
            tool_call_id=last.tool_call_id,
            metadata=last.metadata,
            state=last.state,
        )

    async def finish_on(
        self, answer: MessageDraft, *, run: RunIdentity, finalizer: RunFinalizer
    ) -> None:
        """End a resumed run on the answer its previous attempt committed."""
        event = AgentEvent(
            type=AgentEventType.MESSAGE, data=answer, agent_run_id=run.agent_run_id
        )
        final_status, final_error = self.message_writer.final_status_from_event(event)
        await finalizer.finish(
            run=run,
            status=(
                AgentRunStatus.FAILED
                if final_status == ConversationStatus.FAILED
                else AgentRunStatus.COMPLETED
            ),
            conversation_status=final_status,
            error=final_error,
            output_data=self.message_writer.output_data_from_event(event),
        )

    async def settle(
        self,
        *,
        run_id: UUID,
        conversation: Conversation,
        agent: Agent,
        ctx: ToolContext,
    ) -> ReplayReport:
        async with self.uow_factory() as uow:
            calls = await Runs(uow).list_unpaired_tool_calls(run_id)
        report = ReplayReport()
        runtime: ToolRuntime | None = None
        for call in calls:
            if not is_replayable_call(call.tool_name) or not call.tool_call_id:
                continue
            report.unpaired += 1
            args = recorded_args(call)
            if runtime is None:
                runtime = await self._tools(conversation, agent, ctx)
            klass = replay_class(runtime.get(str(call.tool_name)), args)
            if klass is ReplayClass.UNSAFE:
                await self._persist(
                    conversation.id,
                    run_id,
                    call,
                    {"success": False, "error": OUTCOME_UNKNOWN},
                )
                report.unknown += 1
                report.unknown_tools.append(str(call.tool_name))
                continue
            result = await self._run_again(runtime, run_id, ctx, call, args)
            await self._persist(conversation.id, run_id, call, result)
            report.replayed += 1
        if report.unknown:
            await self._tell_the_reader(conversation.id, run_id, report)
        if report.unpaired:
            logger.warning(
                "agent.turn.resumed",
                agent_run_id=str(run_id),
                unpaired_calls=report.unpaired,
                replayed=report.replayed,
                unknown=report.unknown,
            )
        return report

    async def _tell_the_reader(
        self, conversation_id: UUID, run_id: UUID, report: ReplayReport
    ) -> None:
        """Say in the conversation that something may or may not have happened.

        The model is told in its notice; this is for the person, who otherwise
        sees a turn carry on as though nothing had been cut short.
        """
        tools = sorted(set(report.unknown_tools))
        saved = await self.message_writer.notice(
            conversation_id=conversation_id,
            text=INTERRUPTED_NOTICE.format(tools=", ".join(tools)),
            metadata={"source": "turn_resume", "tool_names": list(tools)},
        )
        await publish_conversation_event(
            conversation_id, message_payload(None, message_to_payload(saved))
        )

    async def _tools(
        self, conversation: Conversation, agent: Agent, ctx: ToolContext
    ) -> ToolRuntime:
        """The run's tools, which say how each open call may be settled."""
        return ToolRuntime(
            await specs_for(
                agent=agent,
                conversation=conversation,
                ctx=ctx,
                surface=ToolSurface.RERUN,
                uow_factory=self.uow_factory,
                grants=getattr(ctx, "grant_summary", None),
            )
        )

    async def _run_again(
        self,
        runtime: ToolRuntime,
        run_id: UUID,
        ctx: ToolContext,
        call: Message,
        args: dict[str, object],
    ) -> object:
        tool_name = str(call.tool_name)
        try:
            result = await runtime.call(
                tool_name, args, ctx, tool_call_id=call.tool_call_id
            )
        except Exception as exc:  # noqa: BLE001 - the return reports it to the run
            if is_control_flow_exception(exc):
                raise
            logger.warning(
                "agent.turn.replay_failed.degraded",
                agent_run_id=str(run_id),
                tool_name=tool_name,
                exc_info=True,
            )
            return format_tool_error(tool_name, exc)
        return to_json_value(result)

    async def _persist(
        self,
        conversation_id: UUID,
        run_id: UUID,
        call: Message,
        result: object,
    ) -> None:
        assert call.tool_call_id is not None
        await self.message_writer.persist(
            conversation_id=conversation_id,
            agent_run_id=run_id,
            data=MessageDraft.of_tool_return(
                tool_call_id=call.tool_call_id,
                tool_name=call.tool_name,
                tool_result=to_json_value(result),
            ),
        )


__all__ = [
    "INTERRUPTED_NOTICE",
    "OUTCOME_UNKNOWN",
    "ReplayReport",
    "TurnResume",
    "recorded_args",
]
