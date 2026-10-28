"""The cache gate: every model request starts with the whole of the one before it.

A provider's prompt cache is keyed on the request prefix. Within a run pydantic-ai
only ever appends, so each request can re-read the last one from cache -- unless
something rewrites what was already sent. Across runs nothing is automatic: the
next run rebuilds its input from storage, and every byte the last run sent that
storage does not reproduce is where the cached prefix ends and the whole rest of
the conversation is paid for again.

This drives the real in-process harness through one scripted conversation of
four runs, storing between runs what the runner stores, and records every
request the model is sent as each protocol we speak renders it on the wire
(OpenAI-compatible chat and Anthropic messages), with the instructions and tool
definitions in front of it. The conversation carries everything that has broken
the prefix before:

- a response with a thought (signed), a preamble and a tool call whose arguments
  are not in canonical JSON, answered by a result whose keys are not sorted;
- a message the person sent while the run worked, steered into it;
- between runs, a contact's message held for a person and a copy a contact
  merge folded in;
- a pause (``ask_user``) and the run that resumes from its answer;
- a compaction, and a later run past the same threshold;
- a table created between runs, which changes the pod inventory in the
  conversation's context.

The rule: each request is a prefix of the next one -- within a run and across
runs, the run-context part and the instructions included. The one exception is
the request a compaction rewrites, once; the run after it must start from what
the compaction left, and make no second summary call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.entities import Agent, Conversation, Message
from app.modules.agent.domain.folded_turns import framed_folds
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.message_state import MessageState
from app.modules.agent.domain.run_log import (
    ReplayStep,
    RunLogReplay,
    RunStepDraft,
    RunStepKind,
    open_tool_call_ids,
    pending_rows,
)
from app.modules.agent.domain.state_kinds import PauseKind, QueueState
from app.modules.agent.domain.tools import (
    AgentInputRequired,
    ToolGroup,
    ToolPolicy,
    tool,
)
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harness.lemma.history_compaction import (
    SUMMARY_MARKER,
)
from app.modules.agent.infrastructure.harness.lemma.pending_user_messages import (
    ClaimSteered,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
_RUN_CONTEXT = "<run_context>"
_SUMMARY_INSTRUCTIONS = "You are compacting"
#: Big enough that two of them push the history past the compaction trigger.
_BULK = "lorem ipsum dolor sit amet " * 400


# -- Tools ------------------------------------------------------------------


class Lookup(BaseModel):
    city: str
    days: list[int]


async def lookup(ctx: ToolContext, request: Lookup) -> dict[str, object]:
    """Look up what there is to do in a city."""
    if request.city == "bulk":
        return {"zeta": _BULK, "alpha": 1}
    # Keys deliberately not in the order a JSONB column stores them.
    return {"zeta": "fjords", "alpha": {"yes": 2, "b": [3]}, "mid": None}


class Question(BaseModel):
    question: str


async def ask_user(ctx: ToolContext, request: Question) -> str:
    """Ask the person something and wait for the answer."""
    assert ctx.agent_run_id is not None
    raise AgentInputRequired(_ASK_ID, PauseKind.QUESTION)


_ASK_ID = "call_ask"
_TOOLS = [
    tool(lookup, ToolGroup.CALLABLES),
    tool(ask_user, ToolGroup.CALLABLES, ToolPolicy(pauses=PauseKind.QUESTION)),
]


# -- What reached the model --------------------------------------------------


@dataclass(slots=True)
class Sent:
    """One request, as recorded when the model was called."""

    run: int
    messages: list[ModelMessage]
    instructions: str | None
    tools: str


@dataclass(slots=True)
class Model:
    """A scripted model: answers each run's script in turn, keeps every request."""

    script: list[ModelResponse] = field(default_factory=list)
    run: int = 0
    sent: list[Sent] = field(default_factory=list)
    summaries: list[int] = field(default_factory=list)
    #: Called after each answer: what happens in the world while the run works.
    meanwhile: Callable[[Model], None] | None = None
    #: What the person sent while the run worked, as the run claims it.
    claim: ClaimSteered | None = None
    #: The pod inventory the conversation's context reports, as the runner reads it.
    brief: str = "## Tables\n(none yet)"

    async def summarize(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        """The compaction's own call: not a request of the conversation."""
        assert (info.instructions or "").startswith(_SUMMARY_INSTRUCTIONS)
        self.summaries.append(self.run)
        return _text("The trip is Oslo, three days, hotel booked.")

    async def stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        self.sent.append(
            Sent(
                run=self.run,
                messages=list(messages),
                instructions=info.instructions,
                tools=json.dumps(
                    [
                        [tool.name, tool.parameters_json_schema]
                        for tool in [*info.function_tools, *info.output_tools]
                    ],
                    sort_keys=True,
                ),
            )
        )
        response = self.script.pop(0)
        for part in response.parts:
            yield _delta(part)
        if self.meanwhile is not None:
            self.meanwhile(self)


def _delta(part: object) -> object:
    from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall

    if isinstance(part, TextPart):
        return part.content
    if isinstance(part, ThinkingPart):
        return {0: DeltaThinkingPart(content=part.content, signature=part.signature)}
    assert isinstance(part, ToolCallPart)
    assert isinstance(part.args, str)
    return {
        1: DeltaToolCall(
            name=part.tool_name, json_args=part.args, tool_call_id=part.tool_call_id
        )
    }


# -- Storage, as the runner keeps it ----------------------------------------


def _jsonb(value: object) -> object:
    """A value as a JSONB column hands it back: keys by length, then bytes."""
    if isinstance(value, dict):
        return {
            key: _jsonb(value[key])
            for key in sorted(value, key=lambda key: (len(key), key))
        }
    if isinstance(value, list):
        return [_jsonb(item) for item in value]
    return value


@dataclass(slots=True)
class Storage:
    """The conversation's rows and run log, written the way the runner writes them."""

    conversation: Conversation
    rows: list[Message] = field(default_factory=list)
    log: list[ReplayStep] = field(default_factory=list)

    def _next(self) -> tuple[int, datetime]:
        sequence = len(self.rows) + 1
        return sequence, _T0 + timedelta(minutes=sequence)

    def write(self, draft: MessageDraft, *, run_id: UUID | None) -> Message:
        sequence, at = self._next()
        row = Message(
            conversation_id=self.conversation.id,
            sequence=sequence,
            agent_run_id=run_id,
            created_at=at,
            role=draft.role,
            kind=draft.kind,
            text=draft.text,
            tool_name=draft.tool_name,
            tool_call_id=draft.tool_call_id,
            tool_args=_jsonb(draft.tool_args),
            tool_result=_jsonb(draft.tool_result),
            metadata=draft.metadata,
            state=draft.state,
        )
        self.rows.append(row)
        return row

    def user(
        self, text: str, *, run_id: UUID | None, state: MessageState | None = None
    ) -> Message:
        return self.write(
            MessageDraft.of_text(text, role=MessageRole.USER, state=state),
            run_id=run_id,
        )

    def store(self, events: Sequence[AgentEvent], *, run_id: UUID) -> None:
        """As the event pump does: rows, and log steps covering them."""
        written: list[UUID] = []
        for event in events:
            if event.type is AgentEventType.MESSAGE and isinstance(
                event.data, MessageDraft
            ):
                written.append(self.write(event.data, run_id=run_id).id)
            elif event.type is AgentEventType.RUN_STEP:
                assert isinstance(event.data, RunStepDraft)
                self._append(event.data, run_id, covers=[*event.data.covers, *written])
                written = []

    def _append(self, draft: RunStepDraft, run_id: UUID, *, covers: list[UUID]) -> None:
        seq = (self.log[-1].seq if self.log else 0) + 1
        if draft.kind is RunStepKind.COMPACTION:
            self.log = []
        self.log.append(ReplayStep(seq, draft.kind, run_id, draft.messages))
        carried = set(covers)
        self.rows = [
            row.model_copy(update={"run_step_seq": seq})
            if row.id in carried and row.run_step_seq is None
            else row
            for row in self.rows
        ]

    def history(self) -> list[Message]:
        return framed_folds(sorted(self.rows, key=lambda row: row.sequence))

    def replay(self) -> RunLogReplay:
        """What the runner hands a run: the log and what it does not carry."""
        open_ids = open_tool_call_ids(step.messages for step in self.log)
        returns: dict[str, Message] = {}
        for row in self.rows:
            if row.kind is MessageKind.TOOL_RETURN and row.tool_call_id in open_ids:
                returns.setdefault(row.tool_call_id, row)
        return RunLogReplay(
            steps=list(self.log),
            pending=pending_rows(self.history(), fallback=not self.log),
            returns=returns,
        )


# -- The conversation --------------------------------------------------------


@dataclass(slots=True)
class Played:
    sent: list[Sent]
    summaries: list[int]


def _thought_call_response() -> ModelResponse:
    return ModelResponse(
        parts=[
            ThinkingPart(content="They want a plan.", signature="sig-1"),
            TextPart("Let me look that up."),
            # Spaced and unsorted, as models write arguments.
            ToolCallPart(
                "lookup",
                '{"days": [1, 2],  "city": "Oslo"}',
                tool_call_id="call_look",
            ),
        ]
    )


def _call(name: str, args: str, call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=call_id)])


def _text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


async def _run(
    model: Model,
    storage: Storage,
    *,
    agent: Agent,
    run_id: UUID,
    script: list[ModelResponse],
    trigger_tokens: int,
) -> list[AgentEvent]:
    model.run += 1
    model.script = list(script)
    conversation = storage.conversation
    ctx = ToolContext(
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
        conversation_id=conversation.id,
        agent_name=agent.name,
        agent_run_id=run_id,
        run_context=f"{_RUN_CONTEXT}\nrun {model.run}\n</run_context>",
        context_brief=model.brief,
    )
    harness = PydanticAIHarness(
        model=FunctionModel(model.summarize, stream_function=model.stream),
        claim_steered=model.claim,
    )
    events: list[AgentEvent] = []
    async for event in harness.run(
        agent=agent,
        conversation=conversation,
        messages=storage.history(),
        ctx=ctx,
        options=HarnessOptions(
            model_name="scripted",
            tools=list(_TOOLS),
            history_summarization_token_limit=trigger_tokens,
            history_summarization_keep_messages=4,
            history_hard_token_ceiling=trigger_tokens * 4,
            run_log=storage.replay(),
        ),
        agent_run_id=run_id,
    ):
        events.append(event)
    storage.store(events, run_id=run_id)
    assert model.script == [], f"run {model.run} left its script unread"
    return events


async def _play() -> Played:
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="planner",
        instruction="Plan trips.",
    )
    storage = Storage(conversation)
    model = Model()
    steered: list[Message] = []

    async def claim(run_id: UUID) -> list[Message]:
        taken, steered[:] = list(steered), []
        return taken

    model.claim = claim

    # Measured once: what every request carries besides its history.
    trigger = await _fixed_tokens(agent, conversation) + 3_000

    # Run 1: a thought, a preamble and a call; a message steered in mid-run.
    run1 = uuid4()
    storage.user("Plan me three days in Oslo.", run_id=run1)
    script1 = [_thought_call_response(), _text("Oslo it is: fjords.")]

    def steer(model: Model) -> None:
        if model.run == 1 and len(model.sent) == 1:
            steered.append(
                storage.user(
                    "Also book a hotel.",
                    run_id=run1,
                    state=MessageState(queue_state=QueueState.STEERED),
                )
            )

    model.meanwhile = steer
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run1,
        script=script1,
        trigger_tokens=trigger,
    )

    # Between runs: held for a person, and a copy a merge folded in.
    storage.write(
        MessageDraft.of_text(
            "Are you there?",
            role=MessageRole.USER,
            state=MessageState(held_for_a_person=True),
        ),
        run_id=None,
    )
    storage.write(
        MessageDraft.of_text(
            "I prefer the old town.",
            role=MessageRole.USER,
            state=MessageState(folded_from=uuid4()),
        ),
        run_id=None,
    )

    # Run 2: asks, and pauses.
    run2 = uuid4()
    storage.user("Which dates suit me?", run_id=run2)
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run2,
        script=[_call("ask_user", '{"question": "Which dates?"}', _ASK_ID)],
        trigger_tokens=trigger,
    )

    # The answer, and the run that resumes from it -- long enough to compact.
    storage.write(
        MessageDraft.of_tool_return(
            tool_call_id=_ASK_ID, tool_name="ask_user", tool_result="The 3rd to 5th."
        ),
        run_id=run2,
    )
    run3 = uuid4()
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run3,
        script=[
            _call("lookup", '{"city": "bulk", "days": [3]}', "call_bulk_1"),
            _call("lookup", '{"city": "bulk", "days": [4]}', "call_bulk_2"),
            _text("Booked for the 3rd to 5th."),
        ],
        trigger_tokens=trigger,
    )

    # Run 4: past the same threshold, it starts from what the compaction left;
    # and a table was created since, so the pod's inventory changed.
    model.brief = _NEW_TABLE
    run4 = uuid4()
    storage.user("Thanks!", run_id=run4)
    await _run(
        model,
        storage,
        agent=agent,
        run_id=run4,
        script=[_text("Enjoy Oslo.")],
        trigger_tokens=trigger,
    )
    return Played(sent=model.sent, summaries=model.summaries)


_NEW_TABLE = "## Tables\nbookings(city, day)"


async def _fixed_tokens(agent: Agent, conversation: Conversation) -> int:
    """The instructions and tool schemas every request carries, in tokens."""
    from app.modules.agent.infrastructure.harness.lemma.context_window import (
        ContextFootprint,
    )

    seen: list[int] = []

    async def answer(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str]:
        footprint = ContextFootprint()
        seen.append(
            footprint.measure(
                info.instructions, [*info.function_tools, *info.output_tools]
            )
        )
        yield "ok"

    async for _ in PydanticAIHarness(model=FunctionModel(stream_function=answer)).run(
        agent=agent,
        conversation=conversation,
        messages=[],
        ctx=ToolContext(
            user_id=conversation.user_id,
            pod_id=conversation.pod_id,
            conversation_id=conversation.id,
            agent_name=agent.name,
        ),
        options=HarnessOptions(
            model_name="scripted",
            tools=list(_TOOLS),
            history_summarization_enabled=False,
        ),
        agent_run_id=uuid4(),
    ):
        pass
    return seen[0]


# -- The wire ---------------------------------------------------------------

Protocol = Literal["openai", "openai-reasoning", "anthropic"]

#: An OpenAI-compatible reasoning model that takes its thoughts back in their
#: own field, as DeepSeek and Kimi on Fireworks do.
_REASONING_FIELD = {
    "openai_chat_thinking_field": "reasoning_content",
    "openai_chat_send_back_thinking_parts": "field",
}


async def _wire(messages: list[ModelMessage], protocol: Protocol) -> list[str]:
    """The request's history as the provider receives it, one block per entry."""
    parameters = ModelRequestParameters()
    if protocol != "anthropic":
        model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="unused"))
        if protocol == "openai-reasoning":
            model = OpenAIChatModel(
                "deepseek",
                provider=OpenAIProvider(api_key="unused"),
                profile={**model.profile, **_REASONING_FIELD},  # type: ignore[typeddict-item]
            )
        mapped = await model._map_messages(messages, parameters)
        # The instructions are compared on their own; this is the history.
        return [
            json.dumps(entry, sort_keys=True, default=str)
            for entry in mapped
            if entry["role"] not in ("system", "developer")
        ]
    anthropic = AnthropicModel(
        "claude-sonnet-4-5", provider=AnthropicProvider(api_key="unused")
    )
    _, params = await anthropic._map_message(messages, parameters, {})
    blocks: list[str] = []
    for message in params:
        content = message["content"]
        items = content if isinstance(content, list) else [content]
        blocks.extend(
            json.dumps([message["role"], item], sort_keys=True, default=str)
            for item in items
        )
    return blocks


@dataclass(slots=True)
class Break:
    after: int
    run: int
    next_run: int
    at: int
    was: str
    became: str

    def __str__(self) -> str:
        where = (
            f"run {self.run}"
            if self.run == self.next_run
            else f"run {self.run} -> run {self.next_run}"
        )
        return (
            f"request {self.after + 1} -> {self.after + 2} ({where}), block "
            f"{self.at}:\n  sent   {self.was[:240]}\n  then   {self.became[:240]}"
        )


async def _breaks(played: Played, protocol: Protocol) -> list[Break]:
    """Where a request does not start with the one before it."""
    found: list[Break] = []
    for index, (earlier, later) in enumerate(zip(played.sent, played.sent[1:])):
        before = await _wire(earlier.messages, protocol)
        after = await _wire(later.messages, protocol)
        if earlier.instructions != later.instructions:
            found.append(
                Break(index, earlier.run, later.run, -1, "instructions", "changed")
            )
        if earlier.tools != later.tools:
            found.append(Break(index, earlier.run, later.run, -1, "tools", "changed"))
        for at, block in enumerate(before):
            if at >= len(after) or after[at] != block:
                became = after[at] if at < len(after) else "(nothing)"
                found.append(Break(index, earlier.run, later.run, at, block, became))
                break
    return [item for item in found if not _is_compaction(played, item)]


def _carries_summary(sent: Sent) -> bool:
    return any(
        SUMMARY_MARKER in json.dumps(part.content)
        for message in sent.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if hasattr(part, "content")
    )


def _is_compaction(played: Played, item: Break) -> bool:
    """The one break allowed: the request a compaction rewrote."""
    return _carries_summary(played.sent[item.after + 1]) and not _carries_summary(
        played.sent[item.after]
    )


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["openai", "openai-reasoning", "anthropic"])
async def test_every_request_starts_with_the_one_before(protocol: Protocol) -> None:
    played = await _play()

    assert [sent.run for sent in played.sent] == [1, 1, 2, 3, 3, 3, 4]
    breaks = await _breaks(played, protocol)
    assert not breaks, "\n\n".join(str(item) for item in breaks)


@pytest.mark.anyio
async def test_the_run_context_part_stays_at_its_turn() -> None:
    """Each run's context is written once, just before its turn, and stays."""
    played = await _play()

    last = played.sent[-1].messages
    texts = [
        part.content
        for message in last
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, str)
    ]
    for turn, run in (("Plan me three days in Oslo.", 1), ("Thanks!", 4)):
        at = next(i for i, text in enumerate(texts) if text.endswith(turn))
        assert texts[at - 1].startswith(f"{_RUN_CONTEXT}\nrun {run}")


@pytest.mark.anyio
async def test_a_table_created_mid_conversation_is_appended_not_rewritten() -> None:
    """The inventory sits in the conversation's context, frozen where the
    conversation started: a new table arrives after everything already sent."""
    played = await _play()

    first, last = played.sent[0], played.sent[-1]
    assert first.instructions == last.instructions
    assert first.instructions is not None and "(none yet)" in first.instructions
    assert _NEW_TABLE not in first.instructions
    wire = await _wire(last.messages, "openai")
    table = "bookings(city, day)"
    assert table in wire[-1] and "Thanks!" in wire[-2]
    assert not any(table in block for block in wire[:-1])


@pytest.mark.anyio
async def test_a_new_conversation_reuses_the_prefix_up_to_the_agent() -> None:
    """Two conversations with one agent share every byte up to the end of the
    agent's own instruction; the conversation's context follows it."""
    agent_instruction = "Plan trips."

    async def first_instructions(brief: str) -> str:
        conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
        agent = Agent(
            pod_id=conversation.pod_id,
            user_id=conversation.user_id,
            name="planner",
            instruction=agent_instruction,
        )
        storage = Storage(conversation)
        model = Model(brief=brief)
        run_id = uuid4()
        storage.user("Hello.", run_id=run_id)
        await _run(
            model,
            storage,
            agent=agent,
            run_id=run_id,
            script=[_text("Hi.")],
            trigger_tokens=10**6,
        )
        assert model.sent[0].instructions is not None
        return model.sent[0].instructions

    one = await first_instructions("## Tables\nflights(no)")
    two = await first_instructions("## Tables\ntrains(no)")
    shared = one.index(agent_instruction) + len(agent_instruction)
    assert one[:shared] == two[:shared]
    assert one[shared:] != two[shared:]
    assert "flights" in one[shared:]


@pytest.mark.anyio
async def test_a_conversation_past_the_threshold_compacts_once() -> None:
    played = await _play()

    # Once, in the run that crossed it: the next run starts from what it left.
    assert played.summaries == [3]
    assert _carries_summary(played.sent[-1])


@pytest.mark.anyio
async def test_a_history_of_pasted_text_compacts_once_and_shrinks() -> None:
    """Live, a history that was mostly the person's own pasted text compacted
    to a larger one (the turns are kept verbatim) and, still over the trigger,
    compacted again on every turn with a new summary call each time."""
    conversation = Conversation(pod_id=uuid4(), user_id=uuid4(), agent_id=uuid4())
    agent = Agent(
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="reader",
        instruction="Read what they paste.",
    )
    storage = Storage(conversation)
    model = Model()
    trigger = await _fixed_tokens(agent, conversation) + 3_000

    pasted = "the contract says " * 1_200
    for turn in range(3):
        run_id = uuid4()
        storage.user(f"Paste {turn}: {pasted}", run_id=run_id)
        await _run(
            model,
            storage,
            agent=agent,
            run_id=run_id,
            script=[_text(f"Read part {turn}.")],
            trigger_tokens=trigger,
        )

    assert model.summaries == [2], "compacted once, in the run that crossed"
    last = model.sent[-1]
    assert _carries_summary(last)
    breaks = await _breaks(
        Played(sent=model.sent[-2:], summaries=model.summaries),
        "openai",
    )
    assert not breaks, "\n\n".join(str(item) for item in breaks)
