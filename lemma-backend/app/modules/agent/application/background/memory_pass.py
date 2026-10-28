"""The memory pass: what a finished turn leaves in memory, kept after its answer.

A run a person is waiting on is told to write memory only when asked
(``memory_kept_after_turn``): reading and writing memory files before the
answer cost that person several model calls for a fact they had only
mentioned. This is the other half of that bargain. Once such a run completes,
its turn is read back and whatever is worth keeping is written -- by the same
contract the agent writes by, with the same authority, on the same model.

* **The same authority.** The pass calls the agent's own pod file tools, under
  the agent's own context, for the person the run worked for. A grant the agent
  lacks is one the pass lacks, and a write drops the cached memory brief just
  as the agent's would. It reaches less, not more: memory only
  (``tools/pod/memory_pass_tools``).
* **The same model**, unless the deployment names a cheaper one
  (``agent_memory_pass_model``). The run's own runtime, so the turn goes to no
  provider it had not already gone to. Asked for no reasoning, because most
  turns end in "nothing".
* **Cheap first.** It cost a multiple of the answer it followed: every request
  re-sent the whole memory brief and the whole turn, and most of them ended in
  "nothing". So the turn is first read alone -- one request, no memory, no
  tools -- for what in it might be worth keeping, and only a turn with
  something in it reaches the keeper, which reads that short list rather than
  the turn, and is told to batch its reads and its writes.
* **Best effort.** The answer never waited on this. A pass that fails has lost
  what the turn would once have saved before answering; the work runtime logs
  each failed attempt, and a refusal that would only repeat is not retried. A
  keeper that runs out of steps keeps what it wrote, and one that wrote
  nothing is tried again (``MemoryPassUnfinished``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from opentelemetry import metrics

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.agent_memory_paths import agent_memory_paths
from app.modules.agent.domain.entities import Agent, AgentRun, Conversation
from app.modules.agent.domain.memory_pass_turn import (
    TurnMessage,
    render_turn,
    said_by_the_person,
    the_persons_words,
)
from app.modules.agent.domain.outsiders import Audience
from app.modules.agent.domain.prompt.templates import (
    load_memory_pass_prompt,
    load_memory_screen_prompt,
)
from app.modules.agent.domain.runtime_choice import RuntimePurpose
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentToolset,
    HarnessKind,
    JsonObject,
)
from app.modules.agent.infrastructure.finished_turn_queries import (
    FinishedTurnQueries,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
)
from app.modules.agent.services.context_brief.memory import AgentMemoryBriefBuilder
from app.modules.agent.services.conversation_access import resolve_agent
from app.modules.agent.services.generation_settings import unreasoned_model_settings
from app.modules.agent.domain.tools import ToolAccess
from app.modules.agent.domain.background import (
    BackgroundCall,
    BackgroundLimitReached,
    BackgroundLimits,
    ToolReturnRecord,
)
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.services.background_model import BackgroundModel
from app.modules.agent.services.runtime_resolution import (
    RuntimeRequest,
    resolve_runtime,
)
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.pod.memory_pass_tools import (
    MemoryScope,
    memory_pass_specs,
)
from app.modules.usage.contracts.execution import UsageExecutionContext

logger = get_logger(__name__)

meter = metrics.get_meter(__name__)
#: One increment per pass that reached an outcome, labelled with it: whether
#: the passes are keeping anything at all is the question this answers.
memory_pass_counter = meter.create_counter("lemma.agent.memory_passes")

#: What the pass's usage is recorded under.
MEMORY_PASS_SOURCE = "agent_memory"

#: The screen is one request with no tools; a second covers a retry pydantic-ai
#: makes of its own on an output it could not read.
_SCREEN_USAGE_LIMITS = BackgroundLimits(
    request_limit=2,
    input_tokens_limit=60_000,
    output_tokens_limit=2_000,
    count_tokens_before_request=True,
)
#: The longest list of facts the keeper is handed.
_WORTH_KEEPING_MAX_CHARS = 4_000

#: A runaway guard, not a budget. Keeping a fact takes at most a read of the
#: topic files it belongs in, the writes, and the closing line.
_PASS_USAGE_LIMITS = BackgroundLimits(
    request_limit=6,
    input_tokens_limit=150_000,
    output_tokens_limit=8_000,
    count_tokens_before_request=True,
)

#: Retries of retries followed back to the message they all answer.
_RETRIES_FOLLOWED = 3

#: Asked after the turn, where the model reads it last. With the framing alone,
#: a model asked not to reason often decided there was nothing to keep once a
#: long answer stood between what the person said about themselves and the end
#: of the message.
_QUESTION = (
    "What did the person say about themselves, their work or this pod that is "
    "worth keeping? List it, or reply `nothing`."
)
#: What the keeper is asked, after the list it was handed.
_KEEP_QUESTION = "Keep what in that list is new, or reply `nothing`."


class MemoryPassOutcome(StrEnum):
    #: At least one memory file written.
    KEPT = "kept"
    #: The turn was read and nothing in it was worth keeping.
    NOTHING = "nothing"
    #: Nothing to read: the run is gone or did not complete, the person said
    #: nothing in it, or it was not one whose memory is kept after it.
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class _FinishedTurn:
    conversation: Conversation
    agent: Agent
    run: AgentRun
    messages: list[TurnMessage]
    reply_before: str | None


#: ``(run model, organization, person) -> the model the pass runs on``.
type PassModel = Callable[[object, UUID | None, UUID], Awaitable[object]]


async def configured_pass_model(
    run_model: object, organization_id: UUID | None, user_id: UUID
) -> object:
    """``agent_memory_pass_model`` when the deployment names one, else the run's."""
    return await BackgroundModel().model_or(
        RuntimePurpose.MEMORY,
        RuntimeRequest(
            organization_id=organization_id,
            user_id=user_id,
            model_name=agent_settings.agent_memory_pass_model,
        ),
        fallback=run_model,
    )


class OutcomeCounter(Protocol):
    def add(self, amount: int, attributes: dict[str, str] | None = None) -> None: ...


class MemoryPass:
    """Reads one finished turn and keeps what in it is worth remembering.

    The model boundary is a constructor argument defaulting to the real one,
    so a test can script the pass's model without replacing names inside this
    module.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        model_for: Callable[[ResolvedAgentRuntime], object] | None = None,
        pass_model: PassModel | None = None,
        counter: OutcomeCounter = memory_pass_counter,
        background: BackgroundModel | None = None,
    ) -> None:
        self.uow_factory = uow_factory
        self._background = background or BackgroundModel()
        self._model_for = model_for
        self._pass_model = pass_model or configured_pass_model
        self.counter = counter

    async def keep(
        self, *, conversation_id: UUID, run_id: UUID, user_id: UUID
    ) -> MemoryPassOutcome:
        """Keep what ``run_id`` left worth remembering, for ``user_id``."""
        outcome = await self._outcome(
            conversation_id=conversation_id, run_id=run_id, user_id=user_id
        )
        self.counter.add(1, {"outcome": outcome.value})
        logger.info(
            "agent.memory_pass.finished",
            agent_run_id=str(run_id),
            conversation_id=str(conversation_id),
            outcome=outcome.value,
        )
        return outcome

    async def _outcome(
        self, *, conversation_id: UUID, run_id: UUID, user_id: UUID
    ) -> MemoryPassOutcome:
        turn = await self._finished_turn(
            conversation_id=conversation_id, run_id=run_id, user_id=user_id
        )
        if turn is None:
            return MemoryPassOutcome.SKIPPED
        resolved = await self._runtime(turn, user_id=user_id)
        if resolved is None:
            return MemoryPassOutcome.SKIPPED
        return await self._keep(turn, resolved, user_id=user_id)

    async def _finished_turn(
        self, *, conversation_id: UUID, run_id: UUID, user_id: UUID
    ) -> _FinishedTurn | None:
        async with self.uow_factory() as uow:
            conversations = Conversations(uow)
            runs = Runs(uow)
            turns = FinishedTurnQueries(uow.session)
            conversation = await conversations.get_conversation(conversation_id)
            run = await runs.get_agent_run(run_id)
            # Checked again rather than trusted from when the item was written:
            # a conversation can be handed to a contact in between, and a
            # stranger's words are never kept with a member's authority.
            if (
                conversation is None
                or run is None
                or run.conversation_id != conversation_id
                or run.status is not AgentRunStatus.COMPLETED
                or Audience.of(conversation).answers_outsiders
            ):
                return None
            carried, began_on = await _carried_in(runs, turns, run)
            messages = [*carried, *await turns.list_turn_messages(run_id)]
            if not said_by_the_person(messages):
                return None
            agent = await resolve_agent(
                conversation, user_id=user_id, agent_repository=AgentRepository(uow)
            )
            # Memory taken away since the run: nothing may be kept for it now.
            if AgentToolset.MEMORY not in agent.toolsets:
                return None
            reply_before = await turns.reply_before_run(conversation_id, began_on)
        return _FinishedTurn(
            conversation=conversation,
            agent=agent,
            run=run,
            messages=messages,
            reply_before=reply_before,
        )

    async def _runtime(
        self, turn: _FinishedTurn, *, user_id: UUID
    ) -> ResolvedAgentRuntime | None:
        """The runtime the run itself ran on, or None when Lemma does not run it."""
        resolved = await resolve_runtime(
            RuntimeRequest(
                organization_id=turn.conversation.organization_id,
                user_id=user_id,
                configured=turn.run.agent_runtime,
            ),
            purpose=RuntimePurpose.TURN,
            uow_factory=self.uow_factory,
        )
        assert resolved is not None  # a turn's runtime resolves or raises
        # A run is only told to leave memory to the pass when it runs here;
        # one whose runtime has since moved to Agent Host has no model for it.
        return resolved if resolved.harness_kind == HarnessKind.LEMMA else None

    async def _keep(
        self, turn: _FinishedTurn, resolved: ResolvedAgentRuntime, *, user_id: UUID
    ) -> MemoryPassOutcome:
        conversation, agent = turn.conversation, turn.agent
        run_model = (self._model_for or self._run_model)(resolved)
        model = await self._pass_model(run_model, conversation.organization_id, user_id)
        # The run's settings are for the run's model; a configured one gets
        # none, as a configured summarization model does. A strict endpoint
        # that refuses the reasoning field is asked once more without it --
        # nothing was written yet: the refusal is the first request's.
        own_model = model is run_model
        settings: _Settings = (
            unreasoned_model_settings(resolved) if own_model else None,
            (
                unreasoned_model_settings(resolved, ask_no_reasoning=False)
                if own_model
                else None
            ),
        )
        usage = UsageExecutionContext(
            user_id=user_id,
            organization_id=conversation.organization_id,
            pod_id=conversation.pod_id,
            agent_id=agent.id,
            conversation_id=conversation.id,
            source_type=MEMORY_PASS_SOURCE,
            source_id=str(turn.run.id),
        )
        today = datetime.now(UTC).date()
        worth_keeping = await self._screen(
            turn, model, settings=settings, usage=usage, user_id=user_id, today=today
        )
        if worth_keeping is None:
            return MemoryPassOutcome.NOTHING
        return await self._write(
            turn,
            model,
            worth_keeping,
            settings=settings,
            usage=usage,
            user_id=user_id,
            today=today,
        )

    async def _screen(
        self,
        turn: _FinishedTurn,
        model: object,
        *,
        settings: _Settings,
        usage: UsageExecutionContext,
        user_id: UUID,
        today: date,
    ) -> str | None:
        """What in the turn may be worth keeping, or None when nothing is.

        One request, with neither the memory brief nor the tools: the turn and
        the question. Most turns end here.
        """
        transcript = render_turn(
            turn.messages, reply_before=turn.reply_before, today=today
        )
        asked, plain = settings
        answer = await self._background.ask(
            RuntimePurpose.MEMORY,
            model,
            BackgroundCall(
                prompt=f"{transcript}\n\n{_QUESTION}",
                limits=_SCREEN_USAGE_LIMITS,
                instructions=load_memory_screen_prompt(),
                model_settings=asked,
                plain_model_settings=plain,
            ),
            usage=usage,
        )
        return _worth_keeping(answer.output)

    async def _write(
        self,
        turn: _FinishedTurn,
        model: object,
        worth_keeping: str,
        *,
        settings: _Settings,
        usage: UsageExecutionContext,
        user_id: UUID,
        today: date,
    ) -> MemoryPassOutcome:
        """Write what the screen found, where it belongs, unless memory has it.

        Reads the screen's list rather than the turn again: it is what there is
        to keep, at a fraction of the turn's length, and it is re-sent with
        every request the keeper makes.
        """
        conversation, agent = turn.conversation, turn.agent
        memory = await AgentMemoryBriefBuilder(self.uow_factory).build(
            agent=agent, pod_id=conversation.pod_id, user_id=user_id
        )
        tools = memory_pass_specs(MemoryScope(agent_memory_paths(agent)))
        writes = frozenset(
            spec.name for spec in tools if spec.policy.access is ToolAccess.WRITE
        )
        asked, plain = settings
        call = BackgroundCall(
            prompt=(
                f"Today is {today.isoformat()}.\n\n"
                f"Worth keeping from the turn that just ended:\n{worth_keeping}\n\n"
                f"{_KEEP_QUESTION}"
            ),
            limits=_PASS_USAGE_LIMITS,
            instructions=f"{load_memory_pass_prompt()}\n{memory}",
            model_settings=asked,
            plain_model_settings=plain,
            tools=tools,
            deps=_pass_deps(turn, user_id=user_id),
        )
        try:
            answer = await self._background.ask(
                RuntimePurpose.MEMORY, model, call, usage=usage
            )
        except BackgroundLimitReached as reached:
            return _at_the_limit(turn, reached, writes)
        if _wrote_memory(answer.tool_returns, writes):
            return MemoryPassOutcome.KEPT
        return MemoryPassOutcome.NOTHING

    def _run_model(self, resolved: ResolvedAgentRuntime) -> object:
        return self._background.build(
            resolved, fallback_model_name=resolved.model_name_for_harness
        )


def _pass_deps(turn: _FinishedTurn, *, user_id: UUID) -> ToolContext:
    """The agent's own context, for the person the run worked for."""
    conversation, agent = turn.conversation, turn.agent
    return ToolContext(
        user_id=user_id,
        org_id=conversation.organization_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
        agent_name=agent.name,
        agent_run_id=turn.run.id,
        workload_id=agent.id,
        is_pod_default_agent=agent.kind is AgentKind.POD_DEFAULT,
        memory_enabled=True,
    )


def _at_the_limit(
    turn: _FinishedTurn, reached: BackgroundLimitReached, writes: frozenset[str]
) -> MemoryPassOutcome:
    """A keeper that ran out of steps: what it wrote stands, or it tries later.

    A write is whole when it returns -- a file is replaced in one call -- so
    whatever the keeper wrote before the limit is kept, and the pass has kept
    something. One that wrote nothing has lost nothing yet: it is raised as an
    ordinary failure, which the work runtime retries.
    """
    wrote = _wrote_memory(reached.tool_returns, writes)
    logger.warning(
        "agent.memory_pass.limit_reached.degraded",
        agent_run_id=str(turn.run.id),
        kept_before_limit=wrote,
        limit=str(reached),
    )
    if wrote:
        return MemoryPassOutcome.KEPT
    raise MemoryPassUnfinished(str(reached)) from reached


class MemoryPassUnfinished(RuntimeError):
    """The keeper ran out of steps before writing anything; worth another try."""


#: The settings asked for first, and the plain ones a strict endpoint gets.
type _Settings = tuple[JsonObject | None, JsonObject | None]


def _worth_keeping(answer: str) -> str | None:
    """The screen's list, or None when it found nothing."""
    text = answer.strip()
    bare = text.strip("`'\". \n").lower()
    if not bare or bare == "nothing" or bare.startswith("nothing worth"):
        return None
    return text[:_WORTH_KEEPING_MAX_CHARS]


async def _carried_in(
    runs: Runs, turns: FinishedTurnQueries, run: AgentRun
) -> tuple[list[TurnMessage], UUID]:
    """What this turn answers that is stored on other runs, and the run it began on.

    A retry answers the message its failed run was started for, which stays on
    that run -- and on the run that one retried, if it was a retry too. A run
    resumed by the person's answer to ``ask_user`` holds neither the question
    nor the answer: both are on the run that paused, which had its pass before
    the answer came.
    """
    carried: list[TurnMessage] = []
    began = run
    for _ in range(_RETRIES_FOLLOWED):
        # What a run answers when that is stored elsewhere: the failed run a
        # retry replaces (``RetryFailedRun``), and the pausing call
        # a resumed run picks up from (``pause_resume``).
        failed_id = began.state.retried_run_id
        failed = await runs.get_agent_run(failed_id) if failed_id else None
        if failed is None or failed.conversation_id != run.conversation_id:
            break
        carried = [
            *the_persons_words(await turns.list_turn_messages(failed.id)),
            *carried,
        ]
        began = failed
    resumed = run.state.resumed_tool_call_id
    if resumed:
        carried.extend(await turns.list_tool_exchange(run.conversation_id, resumed))
    return carried, began.id


def _wrote_memory(returns: list[ToolReturnRecord], writes: frozenset[str]) -> bool:
    """Whether any write the pass made succeeded."""
    return any(
        returned.tool_name in writes
        and isinstance(returned.content, dict)
        and returned.content.get("success") is True
        for returned in returns
    )


__all__ = [
    "MEMORY_PASS_SOURCE",
    "MemoryPass",
    "MemoryPassOutcome",
    "MemoryPassUnfinished",
    "memory_pass_counter",
]
