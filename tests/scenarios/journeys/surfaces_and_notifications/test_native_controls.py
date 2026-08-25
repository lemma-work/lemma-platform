"""Surfaces and notifications → asking a person something, wherever they are.

An agent that can only ask its question in the web workspace is an agent that
cannot be used from a phone. The promise is that a question or an approval
reaches the person on the platform they are already on, using that platform's
own controls where it has them.

Both scenarios ask for the behaviour in the message, the way somebody would,
rather than installing an instruction on an agent the scenario owns. That is
what lets them run unchanged against a real account on real Telegram, where the
agent is the deployment's standing one — and it is the better test, because the
model choosing to use its question tool is the thing being promised.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Answer where the person already is"),
]

#: Long enough for a queued run against a real model to get all the way to a
#: tool call, which is two turns rather than one.
UNTIL_ASKED = 150.0


async def _asked_with_buttons(reachable, describe: str):
    """Wait for a reply that offers something to press, and give the labels."""
    said = await eventually(
        reachable.replies,
        lambda replies: any(reply.choices for reply in replies),
        describe=describe,
        timeout=UNTIL_ASKED,
    )
    return [choice for reply in said for choice in reply.choices]


@scenario("An agent's question reaches the person as native buttons")
@proves("PS-SURF-021")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_question_is_asked_with_native_controls(reachable):
    needs(MODEL_IS_REAL)

    await reachable.says(
        "Ask me to choose between exactly two options, 'Weekly summary' and "
        "'Full ledger', using your ask-a-question tool. Do not ask anything else."
    )

    offered = await _asked_with_buttons(
        reachable, "the question to arrive with native buttons"
    )

    # Matched by containment, not equality: the product decorates a recommended
    # choice ("⭐ Weekly summary") and adds a free-text escape hatch of its own.
    # Both are the product being helpful, and neither changes the choice.
    for choice in ("Weekly summary", "Full ledger"):
        assert any(choice.lower() in label.lower() for label in offered), (
            f"the agent was asked to offer {choice!r} and the person was not sent "
            f"it as a button; the buttons were {offered or 'absent entirely'}"
        )
    assert any("type a reply" in label.lower() for label in offered), (
        f"a person must be able to answer in their own words as well as by "
        f"pressing a button; the buttons were {offered}"
    )


@scenario("An agent's approval request reaches the person as native buttons")
@proves("PS-SURF-021", "PS-AGENT-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_an_approval_is_offered_with_native_controls(reachable):
    """An approval must arrive as something to press, not a sentence to obey."""
    needs(MODEL_IS_REAL)

    await reachable.says(
        f"Create a table called approvals_probe in the {reachable.pod['name']} pod "
        f"with a single text column named note. Ask me to approve it first."
    )

    offered = await _asked_with_buttons(
        reachable, "the approval to arrive as native controls"
    )

    labels = " ".join(offered).lower()
    assert any(word in labels for word in ("approv", "allow", "yes", "confirm")), (
        f"an approval reached the person without a control to approve it: "
        f"{labels or 'no buttons at all'}"
    )
    # "cancel" is in this list because the product uses it, and it is a refusal
    # a person reads as one. Asserting on a vocabulary the product does not
    # share is how a scenario reports a working control as a missing one.
    assert any(
        word in labels for word in ("den", "reject", "decline", "cancel", "no")
    ), (
        f"an approval reached the person without a control to refuse it: "
        f"{labels or 'no buttons at all'}"
    )
    # Left unapproved on purpose. Live, the pod is the tenant's standing one and
    # a scenario that presses "approve" leaves a table behind in it. What is
    # being proved is that a person is *given the choice* on the surface they
    # are already in.
