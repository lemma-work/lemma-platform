"""Reading a workspace choice out of whatever someone types back.

The parsing is the whole risk here. Every other part of the step can be
retried; wiring a conversation to the wrong workspace cannot, because by the
time anyone notices, a private chat has been talking to a pod its owner never
picked. So the rule these tests hold to is that anything short of an
unambiguous answer reads as a `ChoiceProblem` -- the caller asks again, saying
what was wrong -- while every unambiguous way people actually answer a numbered
list on a phone is read.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    MAX_OFFERED_PODS,
    NEW_POD_KEYWORD,
    ChoiceProblem,
    PodChoice,
    offer_text,
    read_choice,
)


def _offered(*names: str) -> list[dict[str, str]]:
    return [{"id": str(uuid4()), "name": name} for name in names]


def _not_an_answer(reply: str, offered) -> bool:
    return isinstance(read_choice(reply, offered), ChoiceProblem)


def test_a_number_picks_the_pod_at_that_position() -> None:
    offered = _offered("Ops", "Personal", "Research")
    choice = read_choice("2", offered)
    assert isinstance(choice, PodChoice)
    assert str(choice.pod_id) == offered[1]["id"]
    assert choice.new_name is None


def test_the_number_is_read_against_the_list_that_was_shown() -> None:
    """Not against a fresh query -- which is why the list is stored at all.

    A pod created or deleted between the question and the answer reorders a
    re-derived list, and "3" would then mean a different workspace than the one
    the person was looking at when they typed it.
    """
    shown = _offered("Ops", "Personal", "Research")
    later = _offered("Brand new", "Ops", "Personal", "Research")
    assert str(read_choice("1", shown).pod_id) == shown[0]["id"]
    assert str(read_choice("1", later).pod_id) != shown[0]["id"]


def test_a_number_outside_the_list_is_not_an_answer() -> None:
    offered = _offered("Ops", "Personal")
    assert _not_an_answer("0", offered)
    assert _not_an_answer("3", offered)
    assert _not_an_answer("99", offered)


def test_new_with_a_name_asks_for_a_new_workspace() -> None:
    choice = read_choice(f"{NEW_POD_KEYWORD} Client work", _offered("Ops"))
    assert choice is not None
    assert choice.pod_id is None
    assert choice.new_name == "Client work"


def test_new_is_matched_whatever_the_casing() -> None:
    choice = read_choice("NEW Weekend project", _offered("Ops"))
    assert choice is not None and choice.new_name == "Weekend project"


def test_new_without_a_name_is_not_an_answer() -> None:
    """Better to ask again than to invent a name on someone's behalf."""
    assert _not_an_answer(NEW_POD_KEYWORD, _offered("Ops"))
    assert _not_an_answer(f"{NEW_POD_KEYWORD}   ", _offered("Ops"))


def test_ordinary_conversation_is_not_an_answer() -> None:
    offered = _offered("Ops", "Personal")
    for text in ("hey", "what is this", "yes", "", "   ", "Opsy"):
        assert _not_an_answer(text, offered), text


def test_a_number_with_nothing_offered_is_not_an_answer() -> None:
    assert _not_an_answer("1", None)
    assert _not_an_answer("1", [])


def test_the_offer_lists_every_workspace_and_says_how_to_reply() -> None:
    text = offer_text(_offered("Ops", "Personal"))
    assert "1. Ops" in text
    assert "2. Personal" in text
    assert NEW_POD_KEYWORD in text


def test_someone_with_no_workspaces_is_asked_to_name_one() -> None:
    """The empty case still has to lead somewhere, not just say "none"."""
    text = offer_text([])
    assert NEW_POD_KEYWORD in text
    assert "1." not in text


@pytest.mark.parametrize(
    "reply", ["2", "2.", "2)", "#2", " 2 ", "option 2", "Option 2", "no. 2", "2!"]
)
def test_the_ways_people_type_a_number_all_read(reply: str) -> None:
    offered = _offered("Ops", "Personal", "Research")
    choice = read_choice(reply, offered)
    assert isinstance(choice, PodChoice), reply
    assert str(choice.pod_id) == offered[1]["id"]


def test_a_superscript_digit_is_asked_about_not_crashed_on() -> None:
    """`"²".isdigit()` is True and `int("²")` raises -- the step used to crash."""
    problem = read_choice("²", _offered("Ops", "Personal"))
    assert isinstance(problem, ChoiceProblem)
    assert "1–2" in problem.message


def test_a_re_ask_says_what_to_send() -> None:
    problem = read_choice("whatever you think", _offered("Ops", "Personal"))
    assert isinstance(problem, ChoiceProblem)
    assert "didn't catch" in problem.message
    assert "1–2" in problem.message and NEW_POD_KEYWORD in problem.message


def test_the_name_of_a_workspace_picks_it_whatever_the_casing() -> None:
    offered = _offered("Ops", "New York trip")
    choice = read_choice("new york TRIP", offered)
    # It starts with the keyword, and it is still the workspace they named:
    # an exact name beats reading "new" as a request for another one.
    assert isinstance(choice, PodChoice) and str(choice.pod_id) == offered[1]["id"]
    choice = read_choice("ops.", offered)
    assert isinstance(choice, PodChoice) and str(choice.pod_id) == offered[0]["id"]


def test_a_name_two_workspaces_share_asks_for_the_number() -> None:
    offered = [
        {"id": str(uuid4()), "name": "Ada", "organization_name": "Acme"},
        {"id": str(uuid4()), "name": "Ada", "organization_name": "Personal"},
    ]
    problem = read_choice("ada", offered)
    assert isinstance(problem, ChoiceProblem) and "number" in problem.message
    choice = read_choice("Ada · Personal", offered)
    assert isinstance(choice, PodChoice) and str(choice.pod_id) == offered[1]["id"]


def test_repeated_names_are_shown_with_their_organization() -> None:
    offered = [
        {"id": str(uuid4()), "name": "Ada", "organization_name": "Acme"},
        {"id": str(uuid4()), "name": "Ada", "organization_name": "Personal"},
        {"id": str(uuid4()), "name": "Ops", "organization_name": "Acme"},
    ]
    text = offer_text(offered)
    assert "1. Ada · Acme" in text
    assert "2. Ada · Personal" in text
    assert "3. Ops\n" in text


@pytest.mark.parametrize(
    "reply", ["new: Client work", "new - Client work", "New Client work"]
)
def test_new_takes_the_separators_people_use(reply: str) -> None:
    choice = read_choice(reply, _offered("Ops"))
    assert isinstance(choice, PodChoice) and choice.new_name == "Client work"


def test_only_the_first_nine_are_numbered_and_the_rest_can_be_named() -> None:
    offered = _offered(*(f"Pod {index}" for index in range(1, 13)))
    text = offer_text(offered)
    assert f"{MAX_OFFERED_PODS}. Pod {MAX_OFFERED_PODS}" in text
    assert "10. " not in text
    assert "3 more" in text
    assert isinstance(read_choice("10", offered), ChoiceProblem)
    choice = read_choice("pod 11", offered)
    assert isinstance(choice, PodChoice) and str(choice.pod_id) == offered[10]["id"]
