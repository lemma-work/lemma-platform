"""Retiring the cards a new message cancelled, and only those.

The journey is in `tests/e2e/test_approval_tap_outcomes_e2e.py`; these pin the
edges it cannot reach cheaply: which pauses count as cancelled, and that a
platform refusing the edit costs the card its retirement and nothing else.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.modules.agent.contracts import channels as agent_conversations
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.platforms.decision_cards import (
    remember_decision_card,
    take_decision_cards,
)
from app.modules.agent_surfaces.services.card_retirement import (
    retire_cards_answered_in_lemma,
    retire_superseded_cards,
)
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_callback_id,
)
from app.modules.agent_surfaces.services.surface_inbound_message import _start_turn
from app.modules.agent_surfaces.tests.unit.surface_doubles import (
    conversation_operations,  # noqa: F401  (autouse fixture)
)

pytestmark = pytest.mark.asyncio


def _context():
    return SimpleNamespace(
        conversation_id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        agent_name=None,
    )


async def test_a_turn_that_starts_cancels_every_undecided_pause() -> None:
    agent_conversations.undecided_pauses.return_value = ["call-a", "call-b"]

    cancelled = await _start_turn(_context(), "something else", {}, uow=object())

    assert cancelled == ["call-a", "call-b"]


async def test_a_message_joining_a_running_turn_cancels_nothing() -> None:
    """It is handed to the run already going, and nothing is superseded."""
    agent_conversations.undecided_pauses.return_value = ["call-a"]
    agent_conversations.start_surface_turn.return_value = None

    cancelled = await _start_turn(_context(), "and also", {}, uow=object())

    assert cancelled == []


@pytest.fixture
def card_store(monkeypatch):
    """The real handle store, over an in-memory Redis."""
    import fakeredis

    from app.modules.agent_surfaces.platforms import decision_cards

    monkeypatch.setattr(settings, "redis_url", "redis://fake-decision-cards/0")
    store = decision_cards._cards()
    monkeypatch.setattr(
        store, "_redis", fakeredis.FakeAsyncRedis(decode_responses=True)
    )
    return store


async def test_a_platform_refusing_the_edit_leaves_the_rest_to_the_tap(
    card_store,
) -> None:
    """Never raises: the turn has started, and a tap is still answered right."""
    conversation_id = uuid4()
    for tool_call_id, message_id in (("call-a", "7"), ("call-b", "8")):
        await remember_decision_card(
            build_callback_id(conversation_id, tool_call_id),
            DecisionCardHandle(platform="TELEGRAM", channel="1", message_id=message_id),
        )
    adapter = SimpleNamespace(
        retire_decision_card=AsyncMock(
            side_effect=[httpx.ConnectError("refused"), None]
        )
    )

    await retire_superseded_cards(
        adapter=adapter,
        credentials={},
        conversation_id=conversation_id,
        tool_call_ids=["call-a", "call-b", "call-c"],
    )

    retired = [
        call.kwargs["handle"].message_id
        for call in adapter.retire_decision_card.await_args_list
    ]
    assert retired == ["7", "8"], "one refusal stopped the other cards retiring"


async def test_a_handle_is_forgotten_once_it_is_taken(card_store) -> None:
    """The second take finds nothing, so a card is never retired twice."""
    callback_id = build_callback_id(uuid4(), "call-a")
    handle = DecisionCardHandle(platform="TELEGRAM", channel="1", message_id="7")

    await remember_decision_card(callback_id, handle)

    assert await take_decision_cards(callback_id) == [handle]
    assert await take_decision_cards(callback_id) == []


async def test_every_card_of_one_pause_is_taken_in_the_order_posted(
    card_store,
) -> None:
    """A question set goes out as one message per question on some platforms,
    and each is a card that must lose its buttons."""
    callback_id = build_callback_id(uuid4(), "call-a")
    handles = [
        DecisionCardHandle(platform="TELEGRAM", channel="1", message_id=str(n))
        for n in (7, 8, 9)
    ]
    for handle in handles:
        await remember_decision_card(callback_id, handle)

    assert await take_decision_cards(callback_id) == handles


async def test_a_card_remembered_before_the_list_is_still_retired(
    card_store,
) -> None:
    """One handle stored as JSON under the bare callback id, as it was before a
    pause could hold several: still found, once."""
    callback_id = build_callback_id(uuid4(), "call-a")
    handle = DecisionCardHandle(platform="TELEGRAM", channel="1", message_id="7")
    await card_store.set_json(callback_id, handle.model_dump(mode="json"))

    assert await take_decision_cards(callback_id) == [handle]
    assert await take_decision_cards(callback_id) == []


async def test_each_card_is_retired_with_the_reason_its_pause_ended(
    card_store,
) -> None:
    conversation_id = uuid4()
    await remember_decision_card(
        build_callback_id(conversation_id, "call-a"),
        DecisionCardHandle(platform="SLACK", channel="C1", message_id="1.0"),
    )
    adapter = SimpleNamespace(retire_decision_card=AsyncMock())

    await retire_superseded_cards(
        adapter=adapter,
        credentials={},
        conversation_id=conversation_id,
        tool_call_ids=["call-a"],
        reason=CardRetirement.ANSWERED,
    )

    [call] = adapter.retire_decision_card.await_args_list
    assert call.kwargs["reason"] is CardRetirement.ANSWERED


class _Scope:
    """A unit of work that only opens and closes."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None


def _egress_resolving(target):
    resolve = (
        AsyncMock(side_effect=target)
        if isinstance(target, Exception)
        else (AsyncMock(return_value=target))
    )
    return lambda uow: SimpleNamespace(
        delivery=SimpleNamespace(resolve_egress_target=resolve)
    )


async def test_a_web_answer_retires_the_platform_cards_as_answered(
    card_store,
) -> None:
    conversation_id = uuid4()
    await remember_decision_card(
        build_callback_id(conversation_id, "call-a"),
        DecisionCardHandle(platform="SLACK", channel="C1", message_id="1.0"),
    )
    adapter = SimpleNamespace(retire_decision_card=AsyncMock())

    await retire_cards_answered_in_lemma(
        uow_factory=_Scope,
        egress_factory=_egress_resolving(
            SimpleNamespace(adapter=adapter, credentials={})
        ),
        conversation_id=conversation_id,
        tool_call_ids=["call-a"],
    )

    [call] = adapter.retire_decision_card.await_args_list
    assert call.kwargs["reason"] is CardRetirement.ANSWERED


async def test_a_web_answer_in_a_conversation_on_no_platform_retires_nothing(
    card_store,
) -> None:
    conversation_id = uuid4()
    callback_id = build_callback_id(conversation_id, "call-a")
    handle = DecisionCardHandle(platform="SLACK", channel="C1", message_id="1.0")
    await remember_decision_card(callback_id, handle)

    await retire_cards_answered_in_lemma(
        uow_factory=_Scope,
        egress_factory=_egress_resolving(None),
        conversation_id=conversation_id,
        tool_call_ids=["call-a"],
    )

    assert await take_decision_cards(callback_id) == [handle]


async def test_a_web_answer_whose_platform_cannot_be_read_does_not_raise(
    card_store,
) -> None:
    """The decision has committed; the cards keeping their buttons is the cost."""
    conversation_id = uuid4()
    callback_id = build_callback_id(conversation_id, "call-a")
    handle = DecisionCardHandle(platform="SLACK", channel="C1", message_id="1.0")
    await remember_decision_card(callback_id, handle)

    await retire_cards_answered_in_lemma(
        uow_factory=_Scope,
        egress_factory=_egress_resolving(OperationalError("select", {}, Exception())),
        conversation_id=conversation_id,
        tool_call_ids=["call-a"],
    )

    assert await take_decision_cards(callback_id) == [handle]
