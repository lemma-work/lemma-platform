"""Reading a workspace choice out of whatever someone types back.

The parsing is the whole risk here. Every other part of the step can be
retried; wiring a conversation to the wrong workspace cannot, because by the
time anyone notices, a private chat has been talking to a pod its owner never
picked. So the rule these tests hold to is that anything short of an
unambiguous answer reads as "no answer", and the caller asks again.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent_surfaces.services.onboarding_pod_choice import (
    NEW_POD_KEYWORD,
    PodChoice,
    offer_text,
    read_choice,
)


def _offered(*names: str) -> list[dict[str, str]]:
    return [{"id": str(uuid4()), "name": name} for name in names]


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
    assert read_choice("0", offered) is None
    assert read_choice("3", offered) is None
    assert read_choice("99", offered) is None


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
    assert read_choice(NEW_POD_KEYWORD, _offered("Ops")) is None
    assert read_choice(f"{NEW_POD_KEYWORD}   ", _offered("Ops")) is None


def test_ordinary_conversation_is_not_an_answer() -> None:
    offered = _offered("Ops", "Personal")
    for text in ("hey", "what is this", "yes", "", "   ", "Ops"):
        assert read_choice(text, offered) is None, text


def test_a_number_with_nothing_offered_is_not_an_answer() -> None:
    assert read_choice("1", None) is None
    assert read_choice("1", []) is None


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
