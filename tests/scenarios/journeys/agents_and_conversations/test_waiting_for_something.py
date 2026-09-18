"""Agents and conversations → waiting for something to finish.

An agent with nothing to do until something else happens should cost nothing
while it waits. Before this existed the only way to wait was to keep asking —
a shell `sleep`, or the same tool over and over — and each ask was a whole model
call replaying the whole conversation to learn that nothing had changed.

What is under test is the product promise, not the mechanism: the turn ends, the
conversation survives, and the agent comes back on its own knowing why.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.waiting import UNTIL_A_MODEL_ACTS, eventually

pytestmark = [
    journey("Agents and conversations"),
    capability("Wait for something to finish"),
]

# Long enough that the agent has to actually suspend rather than answer through,
# short enough that the scenario is not itself a wait. The floor the tool
# enforces is 30s, for the reason this whole feature exists: every wake replays
# the conversation, so a shorter wait costs more than it saves.
_A_SHORT_WAIT_SECONDS = 35


async def _a_pod_with_a_waiting_agent(world):
    needs(MODEL_IS_REAL)
    alice = await world.person("daniel")
    pod = await alice.works_in("customer-support")
    agent = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=["USER_INTERACTION"],
        instruction=(
            "When somebody asks you to check back later, do not answer "
            "immediately and do not try to sleep in a command. Use your waiting "
            f"tool once, for {_A_SHORT_WAIT_SECONDS} seconds, with a reason "
            "saying what you are waiting for. When you come back, say the words "
            "'checked again' and nothing else."
        ),
    )
    return alice, pod, agent


@scenario("An agent with nothing to do ends its turn and comes back by itself")
@proves("PS-AGENT-023")
@covers("agent.conversation.get", "agent.conversation.message.list")
async def test_a_waiting_agent_ends_its_turn_and_returns(world):
    alice, pod, agent = await _a_pod_with_a_waiting_agent(world)

    conversation = await alice.starts_a_conversation(
        watching=False,
        in_pod=pod,
        with_agent=agent["name"],
        saying="Give it a little time, then check again and tell me.",
    )

    # First: the turn really ends. A run that sat there holding its turn open
    # would pass a "did it answer eventually" check while costing exactly what
    # this feature removes.
    async def has_stopped_running() -> bool:
        state = await alice.opens_conversation(conversation, in_pod=pod)
        return str(state.get("status") or "").upper() == "WAITING"

    await eventually(has_stopped_running, timeout=UNTIL_A_MODEL_ACTS)

    # Then: nobody does anything, and it comes back anyway.
    async def has_answered() -> bool:
        messages = await alice.messages_in(conversation, in_pod=pod)
        return any(
            "checked again" in str(message.get("content") or "").lower()
            for message in messages
        )

    await eventually(
        has_answered,
        timeout=UNTIL_A_MODEL_ACTS + _A_SHORT_WAIT_SECONDS,
    )


@scenario("Stopping a waiting conversation ends the wait rather than leaving it armed")
@proves("PS-AGENT-023")
@covers("agent.conversation.get")
async def test_stopping_a_waiting_conversation_ends_the_wait(world):
    alice, pod, agent = await _a_pod_with_a_waiting_agent(world)

    conversation = await alice.starts_a_conversation(
        watching=False,
        in_pod=pod,
        with_agent=agent["name"],
        saying="Give it a little time, then check again and tell me.",
    )

    async def is_waiting() -> bool:
        state = await alice.opens_conversation(conversation, in_pod=pod)
        return str(state.get("status") or "").upper() == "WAITING"

    await eventually(is_waiting, timeout=UNTIL_A_MODEL_ACTS)

    await alice.api.expect(
        "POST",
        f"/pods/{pod['id']}/conversations/{conversation['id']}/stop",
        status=200,
    )

    # Stop has to reach the wait itself. A conversation that reads STOPPED while
    # its wait stays armed is the worst of both: the person believes it is over,
    # and it speaks again minutes later.
    state = await alice.opens_conversation(conversation, in_pod=pod)
    assert str(state.get("status") or "").upper() == "STOPPED", (
        f"stopping a waiting conversation left it {state.get('status')!r}"
    )
