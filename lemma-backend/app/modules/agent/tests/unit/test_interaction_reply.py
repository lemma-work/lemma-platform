"""What a typed reply to a paused ``ask_user`` / ``request_approval`` means.

The agent owns the vocabulary, so web, CLI and every chat platform agree. The
"not a decision" case is the one worth pinning: it used to be folded into DENY,
which cancelled the action *and* discarded what the person wrote.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agent.contracts.interaction_replies import (
    ask_user_request_dict,
    interpret_reply_to_pending,
    reply_plainly_answers_pending,
)
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.services.interaction_reply import (
    _APPROVE_ONCE_REPLIES,
    _APPROVE_SESSION_REPLIES,
    _DENY_REPLIES,
    classify_approval_reply,
    interpret_reply,
    parse_ask_user_reply,
    reply_plainly_answers,
)

_ONE_QUESTION = {
    "questions": [
        {
            "header": "colour",
            "question": "Which colour?",
            "options": [{"label": "Red"}, {"label": "Blue"}],
            "multiSelect": False,
        }
    ]
}
_TWO_QUESTIONS = {
    "questions": [
        *_ONE_QUESTION["questions"],
        {
            "header": "size",
            "question": "Which size?",
            "options": [{"label": "S"}, {"label": "L"}],
            "multiSelect": False,
        },
    ]
}


@pytest.mark.parametrize(
    "text",
    [
        "approve",
        "Approve",
        "yes",
        "Yes!",
        "y",
        "ok",
        "sure",
        "go ahead",
        "yeah go ahead",
        "do it",
        "go for it",
        "lgtm",
        "sounds good",
        "1",
        "👍",
    ],
)
def test_approval_words_approve_once(text: str) -> None:
    assert classify_approval_reply(text) is AgentRunApprovalDecision.APPROVE_ONCE


@pytest.mark.parametrize(
    "text",
    [
        "approve session",
        "approve for session",
        "always allow",
        "yes to all",
        "dont ask again",
        "don't ask again",
        "don’t ask again",
    ],
)
def test_session_words_approve_for_session(text: str) -> None:
    assert classify_approval_reply(text) is AgentRunApprovalDecision.APPROVE_FOR_SESSION


@pytest.mark.parametrize(
    "text",
    ["deny", "no", "n", "nope", "cancel", "stop", "don't", "never mind", "2", "👎"],
)
def test_denial_words_deny(text: str) -> None:
    assert classify_approval_reply(text) is AgentRunApprovalDecision.DENY


@pytest.mark.parametrize(
    "text",
    [
        "wait, why do you need that?",
        "yes, but only if it's the staging table",
        "actually delete the other one instead",
        "what does that command do?",
        "maybe later",
        "hold on",
        "",
        "   ",
    ],
)
def test_everything_else_is_not_a_decision(text: str) -> None:
    """None, so the caller delivers the message instead of inventing a decision."""
    assert classify_approval_reply(text) is None


def test_a_qualified_yes_is_not_consent() -> None:
    """The reason the sets are exact matches rather than prefixes."""
    assert classify_approval_reply("yes, but only if X") is None


def test_the_three_vocabularies_do_not_overlap() -> None:
    """A phrase in two sets would be decided by lookup order."""
    assert not _APPROVE_ONCE_REPLIES & _APPROVE_SESSION_REPLIES
    assert not _APPROVE_ONCE_REPLIES & _DENY_REPLIES
    assert not _APPROVE_SESSION_REPLIES & _DENY_REPLIES


def test_ask_user_request_dict_accepts_both_persisted_shapes() -> None:
    assert ask_user_request_dict(_ONE_QUESTION) == _ONE_QUESTION
    assert ask_user_request_dict({"request": _ONE_QUESTION}) == _ONE_QUESTION
    assert ask_user_request_dict({"foo": 1}) is None
    assert ask_user_request_dict("nope") is None
    assert ask_user_request_dict(None) is None


class _Option:
    def __init__(self, label: str) -> None:
        self.label = label


def _question(header: str, *labels: str) -> SimpleNamespace:
    return SimpleNamespace(header=header, options=[_Option(label) for label in labels])


def test_a_typed_number_or_label_picks_the_option() -> None:
    questions = [_question("colour", "Red", "Blue")]
    assert parse_ask_user_reply("2", questions) == {"colour": "Blue"}
    assert parse_ask_user_reply(" blue ", questions) == {"colour": "Blue"}


def test_anything_else_is_a_free_form_answer() -> None:
    questions = [_question("colour", "Red", "Blue")]
    assert parse_ask_user_reply("teal", questions) == {"colour": "teal"}
    # Out of range is free text, not an IndexError.
    assert parse_ask_user_reply("9", questions) == {"colour": "9"}


def test_several_questions_all_get_the_same_reply() -> None:
    questions = [_question("a", "x"), _question("b", "y")]
    assert parse_ask_user_reply("both", questions) == {"a": "both", "b": "both"}


def test_no_questions_is_a_bare_answer() -> None:
    assert parse_ask_user_reply("hello", []) == {"answer": "hello"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Red", True), ("red", True), ("2", True), ("3", False), ("teal", False)],
)
def test_only_an_offered_option_or_its_number_plainly_answers(
    text: str, expected: bool
) -> None:
    assert (
        reply_plainly_answers(is_approval=False, tool_args=_ONE_QUESTION, text=text)
        is expected
    )


def test_free_text_never_plainly_answers_a_multi_question_ask() -> None:
    assert not reply_plainly_answers(
        is_approval=False, tool_args=_TWO_QUESTIONS, text="Red"
    )


def test_unparseable_args_never_plainly_answer() -> None:
    assert not reply_plainly_answers(
        is_approval=False, tool_args={"questions": "junk"}, text="Red"
    )
    assert not reply_plainly_answers(is_approval=False, tool_args=None, text="Red")


def test_the_gate_and_the_classifier_share_one_vocabulary() -> None:
    """ "go ahead", "sure", thumbs-up were once reported as no answer at all."""
    for phrase in sorted(
        _APPROVE_ONCE_REPLIES | _APPROVE_SESSION_REPLIES | _DENY_REPLIES
    ):
        assert reply_plainly_answers(is_approval=True, tool_args={}, text=phrase), (
            phrase
        )
    assert not reply_plainly_answers(
        is_approval=True, tool_args={}, text="wait, what does it do?"
    )
    assert not reply_plainly_answers(is_approval=True, tool_args={}, text="  ")


def test_interpreting_an_approval_reply() -> None:
    reply = interpret_reply(is_approval=True, tool_args={}, text="go ahead")
    assert reply.decision is AgentRunApprovalDecision.APPROVE_ONCE
    assert reply.response == {}
    assert (
        interpret_reply(is_approval=True, tool_args={}, text="always").decision
        is AgentRunApprovalDecision.APPROVE_FOR_SESSION
    )
    assert (
        interpret_reply(is_approval=True, tool_args={}, text="nope").decision
        is AgentRunApprovalDecision.DENY
    )
    assert interpret_reply(is_approval=True, tool_args={}, text="why?") is None


def test_interpreting_an_ask_user_reply_records_the_answer() -> None:
    reply = interpret_reply(is_approval=False, tool_args=_ONE_QUESTION, text="2")
    assert reply.decision is AgentRunApprovalDecision.APPROVE_ONCE
    assert reply.response == {"answers": {"colour": "Blue"}}


def test_an_ask_user_with_unreadable_args_still_records_the_words() -> None:
    """Losing the option match is better than losing the answer."""
    reply = interpret_reply(is_approval=False, tool_args={"questions": 7}, text="teal")
    assert reply.response == {"answers": {"answer": "teal"}}


def test_the_pending_interaction_wrappers_read_kind_and_args() -> None:
    approval = SimpleNamespace(is_approval=True, tool_args={})
    ask = SimpleNamespace(is_approval=False, tool_args=_ONE_QUESTION)

    assert reply_plainly_answers_pending(approval, "yes")
    assert not reply_plainly_answers_pending(ask, "yes")
    assert interpret_reply_to_pending(ask, "Red").response == {
        "answers": {"colour": "Red"}
    }
    assert interpret_reply_to_pending(approval, "hmm") is None
