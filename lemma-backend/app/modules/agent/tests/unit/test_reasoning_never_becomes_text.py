"""Reasoning is recorded as reasoning, never as the agent's answer.

The bug these pin: a conversation's fourth turn answered with the model's own
chain of thought. Three things had to line up, and each gets its own section
below, because fixing any one of them alone leaves the others live.

1. LEMMA replayed stored thoughts into the assistant's *content* as `<think>`
   tags, which taught the model that reasoning belongs in the answer. Two such
   turns in the history was enough to flip Fireworks MiniMax M3; from the third
   it stopped using `reasoning_content` at all.
2. pydantic-ai only recognises an inline tag when a single stream delta equals
   the literal `<think>`. Fireworks sends `<`, `think`, `>`, so the whole
   thought became a `TextPart`.
3. A `TextPart` is persisted as `MessageKind.TEXT` and stamped
   `is_final_answer`, so the reasoning became the answer bubble *and* the run's
   output — which is what an agent-as-tool caller receives.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4, uuid7

import pytest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import DEFAULT_THINKING_TAGS
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_message_writer import split_reasoning_drafts

# Built from ordinals so the tags survive tooling that reads source as markup.
OPEN = chr(60) + "think" + chr(62)
CLOSE = chr(60) + "/think" + chr(62)

#: What the OpenAI-compatible providers stamp on a thought, and what
#: pydantic-ai reads to decide the thought goes back in `reasoning_content`.
OPENAI_IDENTITY = {
    "thinking_part_id": "reasoning_content",
    "thinking_provider_name": "openai",
}
#: Anthropic's equivalent. Note it is a *signature*, not an id — the two are not
#: interchangeable, which is the whole reason the rebuild is told its target.
ANTHROPIC_IDENTITY = {
    "thinking_provider_name": "anthropic",
    "thinking_signature": "sig-abc",
}


def _message(sequence, role, kind, text, metadata=None) -> Message:
    return Message.create(
        conversation_id=uuid7(),
        sequence=sequence,
        agent_run_id=uuid7(),
        role=role,
        kind=kind,
        text=text,
        metadata=metadata,
    )


def _conversation(thinking_metadata: dict | None) -> list[Message]:
    """A turn that thought, answered, and was asked something else."""
    return [
        _message(1, MessageRole.USER, MessageKind.TEXT, "what pods do i have?"),
        _message(
            2,
            MessageRole.ASSISTANT,
            MessageKind.THINKING,
            "List them, then summarise.",
            thinking_metadata,
        ),
        _message(3, MessageRole.ASSISTANT, MessageKind.TEXT, "You have 3 pods."),
        _message(4, MessageRole.USER, MessageKind.TEXT, "and tables?"),
    ]


def _openai_wire(messages: list[Message]) -> list[dict]:
    model = OpenAIChatModel(
        "accounts/fireworks/models/minimax-m3",
        provider=OpenAIProvider(
            base_url="https://api.fireworks.ai/inference/v1/", api_key="x"
        ),
    )
    history, _ = history_and_prompt(messages, protocol="OPENAI_COMPATIBLE")
    return asyncio.run(model._map_messages(history, ModelRequestParameters()))


def _anthropic_wire(messages: list[Message]) -> list[dict]:
    model = AnthropicModel("claude-sonnet-4-5", provider=AnthropicProvider(api_key="x"))
    history, _ = history_and_prompt(messages, protocol="ANTHROPIC_COMPATIBLE")
    mapped = asyncio.run(model._map_message(history, ModelRequestParameters(), {}))
    return mapped[-1] if isinstance(mapped, tuple) else mapped


def _assert_no_reasoning_in_content(payload: list[dict]) -> None:
    """No assistant turn may carry a thinking tag in the channel it speaks in.

    Asserted on the payload rather than on our own data structures because the
    bug was invisible in ours: the history held a proper `ThinkingPart` and only
    became `<think>` text inside pydantic-ai's mapping, one layer further down
    than any test was looking.
    """
    for entry in payload:
        rendered = str(entry.get("content") or "")
        assert "<think" not in rendered.lower(), payload


# --- 1. what actually goes on the wire --------------------------------------


def test_a_replayed_thought_goes_in_the_reasoning_field_not_the_answer():
    payload = _openai_wire(_conversation(OPENAI_IDENTITY))

    _assert_no_reasoning_in_content(payload)
    assistant = [entry for entry in payload if entry["role"] == "assistant"]
    assert len(assistant) == 1, "the thought belongs to the turn it preceded"
    assert assistant[0]["reasoning_content"] == "List them, then summarise."
    assert assistant[0]["content"] == "You have 3 pods."


def test_a_thought_with_no_credential_is_left_out_rather_than_inlined():
    """Rows written before the credential was recorded, and agent-host rows.

    Losing the replay costs the model sight of its own earlier reasoning. Not
    losing it costs the user an answer made of reasoning, so this is the
    direction the trade goes.
    """
    payload = _openai_wire(_conversation(None))

    _assert_no_reasoning_in_content(payload)
    assistant = [entry for entry in payload if entry["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "You have 3 pods."}]


def test_an_anthropic_credential_does_not_satisfy_an_openai_target():
    """A model swapped mid-conversation must not resurrect the leak.

    Anthropic stamps a signature and no id; the OpenAI path reads the id. A
    thought carrying only the other provider's credential is unreplayable here,
    and inlining it as tags is exactly the failure being fixed.
    """
    payload = _openai_wire(_conversation(ANTHROPIC_IDENTITY))

    _assert_no_reasoning_in_content(payload)


def test_anthropic_needs_a_signature_and_an_id_will_not_do():
    signed = _anthropic_wire(_conversation(ANTHROPIC_IDENTITY))
    blocks = signed[-1]["content"]
    assert {"thinking", "signature", "type"} <= set(blocks[0])
    assert blocks[0]["type"] == "thinking"

    # The same thought carrying only the OpenAI credential is dropped rather
    # than emitted as a `<thinking>` text block, which is what it used to do.
    unsigned = _anthropic_wire(_conversation(OPENAI_IDENTITY))
    for block in unsigned[-1]["content"]:
        assert "<think" not in str(block.get("text") or "").lower()


def test_a_thought_the_run_never_followed_is_dropped():
    """A trailing thought has no response to ride on.

    It cannot be sent on its own: pydantic-ai maps a response holding only a
    thought to no assistant message at all, so emitting one would be a silent
    no-op in the good case and a `<think>` bubble in the bad one.
    """
    payload = _openai_wire(
        [
            _message(1, MessageRole.USER, MessageKind.TEXT, "hi"),
            _message(
                2,
                MessageRole.ASSISTANT,
                MessageKind.THINKING,
                "dangling",
                OPENAI_IDENTITY,
            ),
        ]
    )

    _assert_no_reasoning_in_content(payload)
    assert [entry["role"] for entry in payload] == ["user"]


def test_the_wire_assertion_catches_the_shape_that_shipped():
    """A positive control, so the assertions above cannot quietly stop biting.

    Everything in this section asserts an *absence*, and an absence passes just
    as happily when the check is looking in the wrong place. So build the exact
    history the old code built — one `ModelResponse` per stored row, the thought
    in its own, carrying no credential — and confirm the assertion rejects it.
    """
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ThinkingPart,
        UserPromptPart,
    )

    model = OpenAIChatModel(
        "accounts/fireworks/models/minimax-m3",
        provider=OpenAIProvider(
            base_url="https://api.fireworks.ai/inference/v1/", api_key="x"
        ),
    )
    as_it_was = [
        ModelRequest(parts=[UserPromptPart(content="what pods do i have?")]),
        ModelResponse(parts=[ThinkingPart(content="List them, then summarise.")]),
        ModelResponse(parts=[TextPart(content="You have 3 pods.")]),
    ]
    payload = asyncio.run(model._map_messages(as_it_was, ModelRequestParameters()))

    with pytest.raises(AssertionError):
        _assert_no_reasoning_in_content(payload)


def test_the_tag_convention_is_still_the_one_we_assume():
    """A canary on pydantic-ai.

    Everything here is written against `<think>`. If a version bump changes the
    default the split still works but the *reason* it works changes, and a
    silent mismatch would put reasoning back in answers.
    """
    assert DEFAULT_THINKING_TAGS == (OPEN, CLOSE)


# --- 2 and 3. reasoning that arrives inline anyway --------------------------


def _text_draft(text: str, metadata: dict | None = None) -> MessageDraft:
    return MessageDraft.of_text(text, metadata=metadata)


def test_reasoning_written_into_the_answer_becomes_a_thought_and_an_answer():
    drafts = split_reasoning_drafts(
        _text_draft(f"{OPEN}\nGeneral knowledge. Answer directly.\n{CLOSE}\n\nParis.")
    )

    assert [draft.kind for draft in drafts] == [
        MessageKind.THINKING,
        MessageKind.TEXT,
    ], "the thought comes first because that is the order it happened in"
    assert drafts[0].text == "General knowledge. Answer directly."
    assert drafts[0].metadata["is_final_answer"] is False
    assert drafts[1].text == "Paris."


def test_an_answer_that_is_only_reasoning_produces_no_answer_at_all():
    """The case that poisoned `output_data`.

    An empty answer is not an answer. Emitting one would stamp it
    `is_final_answer` and publish it as the run's result, which is what a
    subagent or an agent-as-tool caller reads back.
    """
    drafts = split_reasoning_drafts(
        _text_draft(f"{OPEN}All of it was thinking.{CLOSE}")
    )

    assert [draft.kind for draft in drafts] == [MessageKind.THINKING]


def test_an_unclosed_thought_never_becomes_an_answer():
    drafts = split_reasoning_drafts(_text_draft(f"{OPEN}Still working through it"))

    assert [draft.kind for draft in drafts] == [MessageKind.THINKING]


def test_an_ordinary_answer_is_returned_untouched():
    draft = _text_draft("Paris is the capital.", {"is_final_answer": True})

    assert split_reasoning_drafts(draft) == [draft]
    assert split_reasoning_drafts(draft)[0] is draft


def test_a_thought_does_not_inherit_the_answers_structured_output():
    """Answer-shaped metadata belongs to the answer half of the split."""
    drafts = split_reasoning_drafts(
        _text_draft(
            f"{OPEN}deciding{CLOSE}Done.",
            {"structured_output": {"answer": "Done."}, "is_final_answer": True},
        )
    )

    assert "structured_output" not in drafts[0].metadata
    assert drafts[1].metadata["structured_output"] == {"answer": "Done."}


def test_a_thinking_message_is_never_split_again():
    draft = MessageDraft.of_thinking(f"I considered writing {OPEN} here.")

    assert split_reasoning_drafts(draft) == [draft]


@pytest.mark.asyncio
async def test_the_live_stream_re_tags_a_thought_split_across_deltas(monkeypatch):
    """The straddled tag, on the token lane.

    Fireworks sends the opening tag as three deltas, which is precisely what
    pydantic-ai's own splitter misses. Without this the reader watches the
    reasoning type itself out inside the answer bubble.
    """
    from app.modules.agent.services import run_event_pump as pump_module

    published: list[dict] = []

    async def _capture(conversation_id, payload, **kwargs):  # noqa: ARG001
        published.append(payload)

    monkeypatch.setattr(pump_module, "publish_conversation_event", _capture)

    pump = pump_module.RunEventPump(message_writer=None, finalizer=None)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    outcome = pump_module.RunOutcome()

    deltas = ["<", "think", ">", "General knowledge.", CLOSE, "Paris."]
    for delta in deltas:
        await pump.handle(
            event=AgentEvent(
                type=AgentEventType.TOKEN,
                data={"kind": "text", "data": delta},
                agent_run_id=run.agent_run_id,
            ),
            run=run,
            outcome=outcome,
        )
    await pump._drain_tokens(run)

    def _joined(kind: str) -> str:
        return "".join(
            str(frame["data"]) for frame in published if frame.get("kind") == kind
        )

    assert _joined("thinking") == "General knowledge."
    assert _joined("text") == "Paris."
    assert "<think" not in _joined("text").lower()


@pytest.mark.asyncio
async def test_an_empty_control_frame_still_reaches_the_client(monkeypatch):
    """`stream_reset` is a token with no payload, and it is not noise.

    The retry path sends it to say "discard the partial answer on screen". An
    earlier version of the classifier skipped empty frames as uninteresting,
    which dropped the signal and left the abandoned answer sitting above the
    retried one. Caught by the e2e retry journey, pinned here.
    """
    from app.modules.agent.services import run_event_pump as pump_module

    published: list[dict] = []

    async def _capture(conversation_id, payload, **kwargs):  # noqa: ARG001
        published.append(payload)

    monkeypatch.setattr(pump_module, "publish_conversation_event", _capture)

    pump = pump_module.RunEventPump(message_writer=None, finalizer=None)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())

    await pump.handle(
        event=AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": "stream_reset", "data": ""},
            agent_run_id=run.agent_run_id,
        ),
        run=run,
        outcome=pump_module.RunOutcome(),
    )

    assert [frame["kind"] for frame in published] == ["stream_reset"]


@pytest.mark.asyncio
async def test_a_retry_does_not_inherit_the_abandoned_attempts_half_thought(
    monkeypatch,
):
    """A stream that died mid-thought must not colour the replacement.

    The classifier is stateful across a run, and a dropped stream can leave it
    inside an unterminated thought. The retry re-streams from the beginning, so
    without a reset its answer would be published as a continuation of a thought
    that no longer exists -- an empty bubble and a trace nobody asked for.
    """
    from app.modules.agent.services import run_event_pump as pump_module

    published: list[dict] = []

    async def _capture(conversation_id, payload, **kwargs):  # noqa: ARG001
        published.append(payload)

    monkeypatch.setattr(pump_module, "publish_conversation_event", _capture)

    pump = pump_module.RunEventPump(message_writer=None, finalizer=None)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())
    outcome = pump_module.RunOutcome()

    async def _token(kind: str, data: str) -> None:
        await pump.handle(
            event=AgentEvent(
                type=AgentEventType.TOKEN,
                data={"kind": kind, "data": data},
                agent_run_id=run.agent_run_id,
            ),
            run=run,
            outcome=outcome,
        )

    # The attempt dies partway through a thought…
    await _token("text", f"{OPEN}half a thou")
    await _token("stream_reset", "")
    # …and the retry answers plainly.
    await _token("text", "Paris.")
    await pump._drain_tokens(run)

    answer = "".join(
        str(frame["data"]) for frame in published if frame.get("kind") == "text"
    )
    assert answer == "Paris."


@pytest.mark.asyncio
async def test_a_token_the_harness_already_classified_passes_through(monkeypatch):
    """Only the text lane is examined.

    A thinking or tool token is not text, and looking for the convention in a
    channel that does not use it would corrupt tool-call argument streams.
    """
    from app.modules.agent.services import run_event_pump as pump_module

    published: list[dict] = []

    async def _capture(conversation_id, payload, **kwargs):  # noqa: ARG001
        published.append(payload)

    monkeypatch.setattr(pump_module, "publish_conversation_event", _capture)

    pump = pump_module.RunEventPump(message_writer=None, finalizer=None)
    run = RunIdentity(conversation_id=uuid4(), agent_run_id=uuid4())

    await pump.handle(
        event=AgentEvent(
            type=AgentEventType.TOKEN,
            data={"kind": "tool", "data": f'{{"q":"{OPEN}"}}'},
            agent_run_id=run.agent_run_id,
        ),
        run=run,
        outcome=pump_module.RunOutcome(),
    )

    assert published == [
        {
            "type": "token",
            "kind": "tool",
            "agent_run_id": str(run.agent_run_id),
            "data": f'{{"q":"{OPEN}"}}',
        }
    ]
