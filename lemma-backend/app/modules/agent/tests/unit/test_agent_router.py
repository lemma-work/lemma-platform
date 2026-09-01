"""Who answers a message nobody addressed.

The value here is in what never reaches a model: an explicit mention, a reply
in an agent's own subthread, an agent's own message, and the one-agent case.
Those are the guards, and they are what these pin.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.agent.services.agent_router import (
    InboundMessage,
    RosterAgent,
    addressed_agent,
    resolve_router_choice,
    routing_is_needed,
    sole_agent,
)


def _roster(*names: str) -> list[RosterAgent]:
    return [RosterAgent(id=uuid4(), name=name) for name in names]


def test_a_mention_is_settled_without_a_model():
    roster = _roster("scout", "archivist")
    message = InboundMessage(text="do the thing", mentioned_agent_id=roster[1].id)

    assert addressed_agent(message, roster) == roster[1].id
    assert routing_is_needed(message, roster) is False


def test_a_reply_in_an_agents_subthread_counts_as_addressed():
    """Mention once, then talk normally."""
    roster = _roster("scout", "archivist")
    message = InboundMessage(text="and the other one?", subthread_agent_id=roster[0].id)

    assert addressed_agent(message, roster) == roster[0].id


def test_a_mention_of_an_agent_who_is_not_here_addresses_nobody():
    """Otherwise a name typed in a room reaches an agent never added to it."""
    roster = _roster("scout")
    message = InboundMessage(text="@archivist help", mentioned_agent_id=uuid4())

    assert addressed_agent(message, roster) is None


def test_an_agents_own_message_is_never_routed():
    """Agent A posts, the router routes to B, B posts, forever."""
    roster = _roster("scout", "archivist")
    message = InboundMessage(text="here is the answer", author_agent_id=roster[0].id)

    assert routing_is_needed(message, roster) is False


def test_one_agent_never_reaches_the_router():
    """The default conversation, and almost all of them."""
    roster = _roster("scout")
    message = InboundMessage(text="anything")

    assert routing_is_needed(message, roster) is False
    assert sole_agent(roster) == roster[0].id


def test_an_unaddressed_message_with_several_agents_does_reach_it():
    roster = _roster("scout", "archivist")

    assert routing_is_needed(InboundMessage(text="who knows this?"), roster) is True


def test_the_model_choosing_nobody_is_silence():
    roster = _roster("scout", "archivist")

    for answer in ["NONE", "none", "nobody", "", None, "  "]:
        assert resolve_router_choice(answer, roster) is None


def test_an_unrecognised_answer_is_silence_not_a_guess():
    """A near-match is the failure worth avoiding: it invents an addressee."""
    roster = _roster("scout", "archivist")

    assert resolve_router_choice("scou", roster) is None
    assert resolve_router_choice("I think scout should take this", roster) is None
    assert resolve_router_choice("archivist", roster) == roster[1].id
    assert resolve_router_choice("@Scout", roster) == roster[0].id


# --- composition -------------------------------------------------------------


async def test_an_addressed_message_never_reaches_a_model(monkeypatch):
    import app.modules.agent.services.agent_router_model as router_model

    called = False

    async def _never(*_args, **_kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(router_model, "_ask_model", _never)
    roster = _roster("scout", "archivist")
    message = InboundMessage(text="go", mentioned_agent_id=roster[1].id)

    chosen = await router_model.resolve_responder(
        message, roster, user_id=uuid4(), organization_id=None, pod_id=uuid4()
    )

    assert chosen == [roster[1].id]
    assert called is False


async def test_a_router_failure_is_silence(monkeypatch):
    """A router that cannot be reached must not stop the message reaching the
    people in the room."""
    import app.modules.agent.services.agent_router_model as router_model

    async def _explode(**_kwargs):
        raise RuntimeError("no model here")

    monkeypatch.setattr(router_model, "_resolve_runtime", _explode)

    chosen = await router_model.resolve_responder(
        InboundMessage(text="who knows this?"),
        _roster("scout", "archivist"),
        user_id=uuid4(),
        organization_id=None,
        pod_id=uuid4(),
    )

    assert chosen == []


# --- the send path actually asks ---------------------------------------------


def _turn_coordinator():
    from app.modules.agent.services.conversation_turns import TurnCoordinator

    # The router is shown the last few messages, so the repository has to be
    # able to hand them over even when a test does not care what they are.
    conversations = SimpleNamespace(list_messages=AsyncMock(return_value=([], None)))
    return TurnCoordinator(
        SimpleNamespace(), conversations, SimpleNamespace(), None, None, None
    )


def _conversation_with_agents(*agent_ids):
    from app.modules.agent.domain.entities import Conversation
    from app.modules.agent.domain.participants import ConversationParticipant

    conversation = Conversation(user_id=uuid4(), pod_id=uuid4())
    conversation.participants = [
        ConversationParticipant(conversation_id=conversation.id, agent_id=agent_id)
        for agent_id in agent_ids
    ]
    return conversation


async def test_the_send_path_skips_the_router_for_one_agent():
    """The rule is pinned above; this pins that the send path applies it.
    Paying for a model to say "the only agent here" on every message would be
    the whole cost of the feature for none of its value."""
    from app.modules.agent.services.turn_routing import UNROUTED

    conversation = _conversation_with_agents(uuid4())
    routed = await _turn_coordinator()._route_unaddressed(
        conversation,
        content="hi",
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )
    assert routed is UNROUTED


async def test_two_agents_ask_and_silence_is_an_answer(monkeypatch):
    """Silence is distinct from "the question does not arise": one stores the
    message with no run, the other lets the conversation's own agent reply."""
    import app.modules.agent.services.agent_router_model as router_model

    async def _silent(*_args, **_kwargs):
        return []

    monkeypatch.setattr(router_model, "resolve_responder", _silent)
    conversation = _conversation_with_agents(uuid4(), uuid4())
    routed = await _turn_coordinator()._route_unaddressed(
        conversation,
        content="anyone?",
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )
    assert routed is None


async def test_a_chosen_agent_is_returned_whole(monkeypatch):
    import app.modules.agent.services.agent_router_model as router_model

    chosen_id = uuid4()
    chosen = SimpleNamespace(id=chosen_id, name="scout", agent_runtime=None)

    async def _chose(*_args, **_kwargs):
        return [chosen_id]

    monkeypatch.setattr(router_model, "resolve_responder", _chose)
    coordinator = _turn_coordinator()
    coordinator.agent_repository = SimpleNamespace(get=AsyncMock(return_value=chosen))
    conversation = _conversation_with_agents(chosen_id, uuid4())

    routed = await coordinator._route_unaddressed(
        conversation,
        content="anyone?",
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert routed is chosen


async def test_an_agent_deleted_mid_flight_falls_back(monkeypatch):
    """The roster came from the conversation's own rows, so a miss here means
    the agent was deleted between two reads. Dropping the message would be
    worse than letting the conversation's own agent answer."""
    import app.modules.agent.services.agent_router_model as router_model
    from app.modules.agent.services.turn_routing import UNROUTED

    async def _chose(*_args, **_kwargs):
        return [uuid4()]

    monkeypatch.setattr(router_model, "resolve_responder", _chose)
    coordinator = _turn_coordinator()
    coordinator.agent_repository = SimpleNamespace(get=AsyncMock(return_value=None))
    conversation = _conversation_with_agents(uuid4(), uuid4())

    routed = await coordinator._route_unaddressed(
        conversation,
        content="anyone?",
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert routed is UNROUTED


# --- an addressed agent gets its own brief -----------------------------------


async def test_a_named_agent_never_gets_the_pod_assistants_brief(monkeypatch):
    """Which brief an agent gets is decided by the agent that is running, not
    by the conversation it runs in.

    An `@mention` puts a named agent in a conversation whose own agent is the
    pod assistant. While the conversation decided, that agent was handed the
    pod-inventory brief -- which lists the pod's agents, itself included -- so
    it read its own name as somebody else and relayed the message on instead of
    answering it.
    """
    from app.modules.agent.domain.entities import Agent, Conversation
    from app.modules.agent.services.agent_context_brief import (
        AgentContextBriefBuilder,
    )
    import app.modules.agent.services.agent_context_brief as brief_module

    chosen: list[str] = []

    async def _inventory(self, **_kwargs):
        chosen.append("pod_inventory")
        return []

    async def _granted(self, **_kwargs):
        chosen.append("granted_resources")
        return []

    monkeypatch.setattr(AgentContextBriefBuilder, "_pod_inventory", _inventory)
    monkeypatch.setattr(AgentContextBriefBuilder, "_granted_resources", _granted)
    # The cache is keyed on the same boolean, so leave it out of the question.
    monkeypatch.setattr(brief_module, "_get_cached_brief", AsyncMock(return_value=None))
    monkeypatch.setattr(brief_module, "_set_cached_brief", AsyncMock())

    class _Repo:
        def __init__(self, _uow):
            pass

        async def get_pod_name(self, _pod_id):
            return "Test Pod"

        async def get_user_profile(self, _user_id):
            return SimpleNamespace(
                email="tester@example.com",
                display_name="Tester",
                first_name="Tester",
                last_name=None,
                timezone=None,
            )

    monkeypatch.setattr(brief_module, "AgentContextBriefRepository", _Repo)

    class _Uow:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *_exc):
            return False

    # A pod-default conversation answered by a *named* agent -- the shape an
    # `@mention` creates.
    conversation = Conversation(user_id=uuid4(), pod_id=uuid4())
    assert conversation.is_pod_assistant
    named = Agent(
        id=uuid4(),
        pod_id=conversation.pod_id,
        user_id=conversation.user_id,
        name="batman",
        instruction="You are batman.",
    )

    await AgentContextBriefBuilder(lambda: _Uow()).build(
        agent=named,
        conversation=conversation,
        user_id=conversation.user_id,
        pod_id=conversation.pod_id,
    )

    assert chosen == ["granted_resources"], chosen


# --- the answer must be separable from the thinking --------------------------


async def test_the_router_asks_for_a_structured_choice(monkeypatch):
    """The choice comes back in a field, never as prose.

    This shipped asking for free text with a 32-token cap, which looked frugal
    and was the whole bug: a reasoning model narrates before it answers, so
    every reply was truncated mid-sentence and the name never arrived. The
    tolerant parser then correctly read the prose as "no choice", and every
    message routed to nobody.
    """
    import app.modules.agent.services.agent_router_model as router_model

    captured: dict[str, object] = {}

    class _FakeAgent:
        def __init__(self, _model, *, system_prompt, output_type):
            captured["output_type"] = output_type
            captured["system_prompt"] = system_prompt

        async def run(self, _prompt, **_kwargs):
            return SimpleNamespace(
                output=router_model.RouterChoice(agents=["archivist"]), usage=None
            )

    monkeypatch.setattr(router_model, "PydanticAIAgent", _FakeAgent)
    monkeypatch.setattr(
        router_model,
        "_resolve_runtime",
        AsyncMock(
            return_value=SimpleNamespace(
                public_snapshot=lambda: {},
                credentials={},
                model_name_for_harness="test-model",
            )
        ),
    )
    monkeypatch.setattr(
        router_model,
        "require_pydantic_ai_model_from_runtime_profile",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(router_model, "usage_limits_for", lambda *_a, **_k: None)
    monkeypatch.setattr(router_model, "reserve_usage_for_runtime", AsyncMock())
    monkeypatch.setattr(router_model, "record_pydantic_ai_result_usage", AsyncMock())

    roster = _roster("scout", "archivist")
    chosen = await router_model.resolve_responder(
        InboundMessage(text="who knows this?"),
        roster,
        user_id=uuid4(),
        organization_id=None,
        pod_id=uuid4(),
    )

    assert captured["output_type"] is router_model.RouterChoice
    assert chosen == [roster[1].id]


def test_the_output_budget_leaves_room_to_think():
    """A cap sized to a name truncates a model that reasons before answering."""
    import app.modules.agent.services.agent_router_model as router_model

    assert router_model._ROUTER_USAGE_LIMITS.output_tokens_limit >= 256


# --- an agent knows who else is in the room ----------------------------------


def _participant(*, user_id=None, agent_id=None, name=None):
    from app.modules.agent.domain.participants import ConversationParticipant

    return ConversationParticipant(
        conversation_id=uuid4(),
        user_id=user_id,
        agent_id=agent_id,
        display_name=name,
    )


def test_a_shared_conversation_tells_an_agent_who_is_present():
    """Without this an agent in a shared conversation is blind to the room.
    One replied "it's just me in here, no other agents around" while standing
    next to another agent and two people."""
    from app.modules.agent.domain.prompts import _room_section

    conversation = SimpleNamespace(
        participants=[
            _participant(user_id=uuid4(), name="Deepak"),
            _participant(user_id=uuid4(), name="Sam"),
            _participant(agent_id=uuid4(), name="batman"),
            _participant(agent_id=uuid4(), name="robin"),
        ]
    )

    section = _room_section(conversation, SimpleNamespace(name="robin"))

    assert section is not None
    assert "Deepak" in section and "Sam" in section
    assert "batman" in section and "robin" in section
    assert "You are robin here" in section
    # The behaviour that made this necessary: relaying instead of answering.
    assert "do not relay" in section


def test_a_one_to_one_conversation_gets_no_roster():
    """A section saying the only two participants are the two already talking
    is noise in every prompt of every ordinary conversation."""
    from app.modules.agent.domain.prompts import _room_section

    conversation = SimpleNamespace(
        participants=[_participant(user_id=uuid4(), name="Deepak")]
    )

    assert _room_section(conversation, SimpleNamespace(name="Lem")) is None
