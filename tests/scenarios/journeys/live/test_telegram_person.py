"""Live → a real person, on real Telegram, talking to an agent.

Every other Telegram scenario delivers an update to Lemma's own webhook, which
proves Lemma's half. This proves the whole of it: a real account sends a real
message through Telegram's own infrastructure, the product receives it the way
it receives anybody's, the agent answers, and the answer arrives back in that
account's chat.

That was impossible until there was a person to be. A bot never receives a
message nobody sent it and cannot send one *as* a human, so the suite could
only ever drive the outbound half. `harness/telegram_person.py` signs a real
account in; `harness/telegram_login.py` mints the session, once, by hand.

What these cover, which is what a person actually does on a messaging surface:

* text in, answer out — the ordinary case
* an image in, understood — the agent reads what it was sent, not just the
  caption
* being asked something, and answering by pressing one of the buttons offered
* being asked to approve something, and approving it

Real resources: they run against the deployment's own bot and the account in
`TELEGRAM_SESSION`, and each conversation deletes itself afterwards.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import (
    REAL_MODEL,
    TELEGRAM,
    TELEGRAM_APP,
    TELEGRAM_PERSON,
    needs,
)
from harness.telegram_person import a_person_on_telegram
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
    pytest.mark.live,
]

#: A 1×1 red PNG. Small enough to inline, and a real image — Telegram rejects
#: bytes that are not, and the model is being asked to look at it.
A_RED_DOT = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


async def _bot_handle() -> str:
    """The @username of the deployment's own bot, asked of Telegram."""
    token = TELEGRAM.value("TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=30.0) as client:
        answered = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        answered.raise_for_status()
    username = ((answered.json() or {}).get("result") or {}).get("username")
    if not username:
        raise AssertionError(
            "Telegram would not say who this bot is, so there is nothing for a "
            "person to message. Check TELEGRAM_BOT_TOKEN."
        )
    return f"@{username}"


@pytest.fixture
async def talking(world):
    """A pod on the standing Telegram surface, and a person messaging its bot.

    The surface stands between runs — `tenant.STANDING_REACH` — because a bot
    cannot open a conversation, so the reachability a person's first message
    creates has to outlive the scenario that created it.
    """
    needs(TELEGRAM, TELEGRAM_APP, TELEGRAM_PERSON, REAL_MODEL)
    from harness.tenant import CONNECTOR_HOLDER, STANDING_REACH

    reach = STANDING_REACH[0]
    holder = await world.person(CONNECTOR_HOLDER)
    pod = await holder.works_in(reach.pod)
    surfaces = {str(s.get("name")) for s in await holder.surfaces_in(pod)}
    if reach.name not in surfaces:
        pytest.skip(
            f"the standing {reach.platform} surface is not on {reach.pod!r}; run "
            f"`make scenarios-provision` once the {reach.connector} account is "
            f"connected"
        )

    bot = await _bot_handle()
    person = await a_person_on_telegram()
    try:
        if not person.username:
            pytest.skip(
                "the Telegram account in TELEGRAM_SESSION has no @username, and "
                "that is how Lemma recognises a sender — an inbound message from "
                "it resolves to nobody, so the agent correctly answers a stranger "
                "rather than a colleague. Set a username on that account in "
                "Telegram's settings (Settings → Username); everyone the product "
                "is built for has one."
            )
        # Tell Lemma which colleague this account is. Without it the sender is a
        # stranger — which the product handles well, and is a different promise
        # from the ones these scenarios are about.
        await holder.is_known_on_telegram_as(person.username)
        async with person.talking_to(bot) as chat:
            yield holder, pod, chat
    finally:
        await person.aclose()


@scenario("A person messages the agent on Telegram and is answered there")
@proves("PS-SURF-010", "PS-SURF-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_person_is_answered(talking):
    _holder, _pod, chat = talking

    await chat.says("Hello — reply with a short greeting so I know you are there.")

    answer = await chat.waits_for_a_reply()

    assert answer.text.strip(), (
        "the agent sent an empty message back, which a person reads as the "
        "product being broken"
    )


@scenario("A person sends an image and the agent describes what is in it")
@proves("PS-SURF-011", "PS-AGENT-030")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_an_image_is_understood(talking):
    """The agent must read the picture, not the filename.

    Asserted on the colour rather than on any word in particular: an agent that
    only saw "a photo arrived" cannot say what colour it is, and one that read
    the image can.
    """
    _holder, _pod, chat = talking

    await chat.sends_file(
        "dot.png", A_RED_DOT, caption="What colour is this image? Answer in one word."
    )

    answer = await chat.waits_for_a_reply()

    assert "red" in answer.text.lower(), (
        f"the agent was sent a red image and did not say red: {answer.text[:200]!r}. "
        f"Either the attachment never reached the model, or it was described "
        f"rather than looked at."
    )


@scenario("A person answers the agent's question by pressing a button")
@proves("PS-SURF-021")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_question_is_answered_by_pressing(talking):
    _holder, _pod, chat = talking

    await chat.says(
        "Ask me to choose between exactly two options, 'Weekly summary' and "
        "'Full ledger', using your ask-a-question tool. Do not ask anything else."
    )

    asked = await eventually(
        chat.replies,
        lambda said: any(reply.choices for reply in said),
        describe="the agent's question to arrive with buttons a person can press",
        timeout=150.0,
    )

    offered = [choice for reply in asked for choice in reply.choices]
    assert any("weekly" in choice.lower() for choice in offered), (
        f"the agent asked a question and Telegram was not sent the choices as "
        f"buttons; a person got {offered or 'no buttons at all'}"
    )


@scenario("A person approves what the agent asked permission for")
@proves("PS-SURF-021", "PS-AGENT-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_an_approval_is_offered_to_the_person(talking):
    """An approval must arrive as something to press, not a sentence to obey."""
    _holder, pod, chat = talking

    await chat.says(
        f"Create a table called approvals_probe in the {pod['name']} pod with a "
        f"single text column named note. Ask me to approve it first."
    )

    asked = await eventually(
        chat.replies,
        lambda said: any(reply.choices for reply in said),
        describe="the approval to arrive as native controls",
        timeout=150.0,
    )

    labels = " ".join(choice.lower() for reply in asked for choice in reply.choices)
    assert any(word in labels for word in ("approve", "allow", "yes", "confirm")), (
        f"an approval reached Telegram without a control to approve it: "
        f"{labels or 'no buttons at all'}"
    )
    # Left unapproved on purpose: the pod is the tenant's, and a scenario that
    # presses "approve" leaves a table behind in it. What is being proved is
    # that a person is *given the choice* on the surface they are already in.
