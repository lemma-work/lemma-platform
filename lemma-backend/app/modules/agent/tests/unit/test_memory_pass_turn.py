"""The finished turn as the memory pass reads it: in order, and bounded."""

from __future__ import annotations

from datetime import date

from app.modules.agent.domain.memory_pass_turn import (
    REPLY_BEFORE_MAX_CHARS,
    STEP_MAX_CHARS,
    WORDS_MAX_CHARS,
    WORK_MAX_CHARS,
    TurnMessage,
    render_turn,
    said_by_the_person,
    the_persons_words,
)
from app.modules.agent.domain.value_objects import MessageKind, MessageRole

_TODAY = date(2026, 10, 22)


def _said(text: str) -> TurnMessage:
    return TurnMessage(MessageRole.USER, MessageKind.TEXT, text)


def _answered(text: str) -> TurnMessage:
    return TurnMessage(MessageRole.ASSISTANT, MessageKind.TEXT, text)


def _called(name: str, args: str) -> TurnMessage:
    return TurnMessage(MessageRole.ASSISTANT, MessageKind.TOOL_CALL, args, name)


def _returned(name: str, result: str) -> TurnMessage:
    return TurnMessage(MessageRole.TOOL, MessageKind.TOOL_RETURN, result, name)


def test_a_turn_is_written_out_in_the_order_it_happened():
    rendered = render_turn(
        [
            _said("I run a 12-person design studio. A checklist, please."),
            _called("web_search", '{"query": "client onboarding"}'),
            _returned("web_search", '{"results": []}'),
            _answered("1. Kickoff call"),
        ],
        reply_before="Hello! How can I help?",
        today=_TODAY,
    )

    order = [
        "Today is 2026-10-22.",
        "> Hello! How can I help?",
        "> I run a 12-person design studio.",
        'The agent called `web_search` with {"query": "client onboarding"}',
        '`web_search` returned {"results": []}',
        "> 1. Kickoff call",
    ]
    positions = [rendered.index(line) for line in order]
    assert positions == sorted(positions)


def test_what_the_person_said_survives_work_too_long_to_show():
    """Steps past the total are counted, never what the person said after them."""
    step = "x" * (STEP_MAX_CHARS - 50)
    steps = [_called("pod_query", step) for _ in range(40)]

    rendered = render_turn(
        [*steps, _said("Always invoice in euros."), _answered("Done.")],
        reply_before=None,
        today=_TODAY,
    )

    assert "> Always invoice in euros." in rendered
    assert "> Done." in rendered
    shown = rendered.count("The agent called `pod_query`")
    assert 0 < shown < 40
    assert f"({40 - shown} more steps of the agent's work not shown.)" in rendered
    assert len(rendered) < WORK_MAX_CHARS + 2 * WORDS_MAX_CHARS


def test_a_message_past_its_cap_is_cut_and_says_so():
    rendered = render_turn(
        [_said("a" * (WORDS_MAX_CHARS + 1)), _returned("pod_read_file", "b" * 5000)],
        reply_before="c" * (REPLY_BEFORE_MAX_CHARS + 500),
        today=_TODAY,
    )

    assert rendered.count(" […]") == 3
    assert "a" * WORDS_MAX_CHARS not in rendered
    assert "b" * STEP_MAX_CHARS not in rendered
    assert "c" * REPLY_BEFORE_MAX_CHARS not in rendered


def test_a_turn_that_only_resumed_work_has_nothing_the_person_said():
    resumed = [_called("request_approval", "{}"), _returned("request_approval", "{}")]

    assert not said_by_the_person([*resumed, _answered("Deleted it.")])
    assert not said_by_the_person([_said("   ")])
    assert said_by_the_person([_said("Call me Sam.")])


def test_an_answer_to_the_agents_question_is_the_persons_own_words():
    asked = _called("ask_user", '{"questions": [{"question": "Which city?"}]}')
    answer = _returned("ask_user", '{"answers": {"City": "Lisbon"}}')

    assert said_by_the_person([asked, answer, _answered("Lisbon it is.")])
    assert the_persons_words(
        [_said("Plan the sync."), asked, answer, _answered("Done.")]
    ) == [_said("Plan the sync."), answer]
