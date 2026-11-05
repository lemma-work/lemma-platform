"""A paused ``ask_user`` answered on its line: question by question.

The answer a line takes becomes the paused call's return, keyed by each
question's header. When it asked several, every one needs its own answer --
the old reading took the first header for all of them, so "Small" answered
both "size" and "colour". The rest of the round trip is against Postgres in
``tests/e2e/test_channel_answers_e2e.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.services.pause_questions import (
    PauseDecision,
    ask_response,
    effective_answer,
    parse_tool_ref,
    tool_ref,
)
from app.modules.channels.contracts.questions import AnswerRefused

pytestmark = pytest.mark.unit

TWO = [
    {"question": "Which size?", "header": "size", "options": [{"label": "S"}]},
    {
        "question": "Which colour?",
        "header": "colour",
        "options": [{"label": "Red"}, {"label": "Blue"}],
        "multi_select": True,
    },
]


def test_one_question_takes_a_choice_or_words():
    assert ask_response(TWO[:1], {"choice": "S"}) == {"answers": {"size": "S"}}
    assert ask_response(TWO[:1], {"text": " small "}) == {"answers": {"size": "small"}}


def test_a_choice_is_one_of_the_options():
    """Words of their own are taken as text -- every ask offers "Other" --
    but a choice that is none of the options is refused."""
    with pytest.raises(AnswerRefused) as refused:
        ask_response(TWO[:1], {"choice": "XXL"})
    assert refused.value.status_code == 422
    assert "XXL" in str(refused.value)

    with pytest.raises(AnswerRefused):
        ask_response(TWO, {"answers": {"size": "S", "colour": ["Red", "Green"]}})
    # One answer to a question that takes one.
    with pytest.raises(AnswerRefused) as refused:
        ask_response(TWO[:1], {"answers": {"size": ["S", "S"]}})
    assert "Pick one" in str(refused.value)

    assert ask_response(TWO[:1], {"text": "XXL"}) == {"answers": {"size": "XXL"}}


def test_a_choice_is_matched_whatever_its_case():
    """ "Cats" picks the option "cats", and the agent reads it as the option
    it offered."""
    pets = [{"question": "Which?", "header": "pet", "options": [{"label": "cats"}]}]
    assert ask_response(pets, {"choice": "Cats"}) == {"answers": {"pet": "cats"}}
    assert ask_response(
        TWO, {"answers": {"size": "s", "colour": [" red", "BLUE"]}}
    ) == {"answers": {"size": "s", "colour": ["Red", "Blue"]}}


def test_an_approval_line_shows_the_action_and_masks_its_secrets():
    from app.modules.agent.services.approval_action import MAX_ACTION_CHARS
    from app.modules.agent.services.pause_questions import Pause, shown

    def approval(args: dict) -> Pause:
        return Pause(
            tool_call_id="call",
            kind="request_approval",
            tool_args={
                "title": "Create the table?",
                "reason": "You asked for it.",
                "tool_name": "exec_command",
                "args": args,
            },
            agent_run_id=None,
        )

    text, question = shown(approval({"cmd": "lemma tables create orders"}))
    assert "exec_command" in text and "lemma tables create orders" in text
    assert question["action"] == "exec_command"
    assert question["action_args"] == {"cmd": "lemma tables create orders"}
    assert question["action_text"] == "lemma tables create orders"

    secret = "sk-live-not-a-real-credential-0123456789"
    text, question = shown(
        approval(
            {
                "url": "https://api.example.com/x",
                "api_key": secret,
                "body": f"Authorization: Bearer {secret}",
            }
        )
    )
    assert secret not in text and secret not in str(question)
    assert "https://api.example.com/x" in text

    text, question = shown(approval({"cmd": "echo " + "x" * 10_000}))
    assert len(str(question["action_text"])) <= MAX_ACTION_CHARS
    assert len(text) < MAX_ACTION_CHARS + 200


def test_several_questions_are_answered_field_by_field():
    given = {"answers": {"size": "S", "colour": ["Red", "Blue"]}}

    assert ask_response(TWO, given) == {
        "answers": {"size": "S", "colour": ["Red", "Blue"]}
    }
    # The headers themselves, as a form would send them.
    assert ask_response(TWO, {"size": "S", "colour": "Red"}) == {
        "answers": {"size": "S", "colour": "Red"}
    }


def test_one_answer_is_never_taken_for_several_questions():
    with pytest.raises(AnswerRefused) as refused:
        ask_response(TWO, {"choice": "S"})

    assert refused.value.status_code == 422
    assert "size" in str(refused.value) and "colour" in str(refused.value)


def test_a_question_left_blank_is_named():
    with pytest.raises(AnswerRefused) as refused:
        ask_response(TWO, {"answers": {"size": "S", "colour": "  "}})

    assert "colour" in str(refused.value)
    assert "size" not in str(refused.value)


def test_what_took_effect_is_what_the_line_shows():
    """A concurrent answer that won: the line records its decision."""
    asked = PauseDecision(
        decision="APPROVE_ONCE",
        response={"answers": {"size": "M"}},
        resolved_by_user_id=None,
    )
    denied = PauseDecision(decision="DENY", response={}, resolved_by_user_id=None)

    assert effective_answer("ask_user", asked) == {"answers": {"size": "M"}}
    assert effective_answer("ask_user", denied) == {"decision": "DENY"}
    assert effective_answer("request_approval", denied) == {"decision": "DENY"}


def test_refs_name_their_conversation():
    conversation = uuid4()

    assert parse_tool_ref(tool_ref(conversation, "call-1")) == (conversation, "call-1")
    assert parse_tool_ref("wf:x:y") is None


def test_a_client_id_always_fits_a_line_and_is_stable():
    """Provider tool call ids are long enough to push ``{run}:{call}`` past a
    line's 64-character client id; a digest keeps it the same line on retry."""
    from app.modules.agent.domain.asked_questions import (
        MAX_CLIENT_ID,
        line_client_id,
    )

    short = "run:call"
    long = f"{uuid4()}:toolu_01{'x' * 40}"

    assert line_client_id(short) == short
    assert len(line_client_id(long)) <= MAX_CLIENT_ID
    assert line_client_id(long) == line_client_id(long)
    assert line_client_id(long) != line_client_id(long + "y")
