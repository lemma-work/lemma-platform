"""Surfaces and notifications → asking a person something, wherever they are.

An agent that can only ask its question in the web workspace is an agent that
cannot be used from a phone. The promise is that a question or an approval
reaches the person on the platform they are already on, using that platform's
own controls where it has them.

Telegram is answered by the egress proxy — the product connects to
`api.telegram.org` as it would in production. Lemma runs for real, and the
scenario reads the buttons the platform was actually sent.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.telegram_view import TelegramView
from harness.credentials import needs
from harness.steps.datastore import column
from harness.environment import MODEL_IS_REAL
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Answer where the person already is"),
]


def _update(*, text: str, update_id: int, handle: str, chat_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {
                "id": chat_id,
                "is_bot": False,
                "first_name": "Alice",
                # What makes this a message from somebody rather than a stranger.
                "username": handle,
            },
            "text": text,
        },
    }


@pytest.fixture
async def reachable_pod(world, run, egress):
    """A pod on Telegram, with a person the platform recognises."""
    fake = TelegramView(egress)
    # A Telegram account belongs to one person deployment-wide, and a chat id
    # addresses one conversation, so both are per-scenario. Sharing either would
    # make one scenario's sender collide with another's.
    handle = f"alice_{uuid4().hex[:10]}"
    chat_id = 66600 + (uuid4().int % 9000)
    alice = await world.person("daniel")
    await alice.is_known_on_telegram_as(handle)
    organization = alice.organization
    pod = await alice.creates_a_pod(named=run.name("pod"))
    table = await alice.creates_a_table(in_pod=pod, columns=[column("title")])
    agent = await alice.creates_an_agent(
        in_pod=pod,
        toolsets=["POD", "USER_INTERACTION"],
        # The agent is *told* how to behave, which is what a person does
        # when they set one up — rather than the scenario injecting the tool
        # call it wants to see. The instruction is product surface; the turn
        # the model then takes is the thing under test.
        instruction=(
            "When somebody asks for a report, do not choose for them. Use "
            "your question tool to ask which report they want, offering "
            "exactly two choices: 'Weekly summary' and 'Full ledger'. "
            "Before reading anything from the pod, ask for approval first."
        ),
    )
    auth_config = await alice.installs_connector("telegram", in_organization=organization)
    account = await alice.connects_account(
        in_organization=organization,
        auth_config=auth_config,
        credentials={"bot_token": f"{uuid4().int % 10**10}:scenarios"},
    )
    await alice.connects_a_surface(
        in_pod=pod,
        platform="TELEGRAM",
        named="tg",
        agent=agent["name"],
        account=account,
    )
    yield alice, pod, fake, handle, chat_id, table


async def _says_on_telegram(
    alice, fake, text: str, *, update_id: int, handle: str, chat_id: int
) -> None:
    delivered = await alice.api.call(
        "POST",
        fake.webhook_path,
        json=_update(text=text, update_id=update_id, handle=handle, chat_id=chat_id),
        headers={"X-Telegram-Bot-Api-Secret-Token": fake.webhook_secret},
    )
    assert delivered.status_code < 400, (
        f"a correctly signed delivery was rejected: {delivered.status_code} "
        f"{delivered.text[:300]}"
    )


async def _their_conversation(alice, pod, fake, handle, chat_id):
    """Say hello, and take the thread the surface opened for it."""
    # Update ids are how a platform names a delivery, and Lemma remembers the
    # ones it has already handled — so a scenario reusing another's id has its
    # message correctly discarded as a duplicate and waits forever.
    await _says_on_telegram(
        alice, fake, "hello", update_id=chat_id * 10, handle=handle, chat_id=chat_id
    )
    threads = await eventually(
        lambda: alice.conversations_in(pod),
        bool,
        describe="the surface to open a conversation for the sender",
        timeout=60.0,
    )
    await eventually(
        lambda: _replies(fake, chat_id),
        bool,
        describe="the agent to answer the first message",
        timeout=60.0,
    )
    return threads[0]


async def _replies(fake, chat_id):
    return fake.messages_to(chat_id)


@scenario("An agent's question reaches the person as native buttons")
@proves("PS-SURF-021")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_question_is_asked_with_native_controls(reachable_pod):
    needs(MODEL_IS_REAL)
    alice, pod, fake, handle, chat_id, table = reachable_pod
    conversation = await _their_conversation(alice, pod, fake, handle, chat_id)

    del conversation  # the surface owns the thread; this scenario just talks
    await _says_on_telegram(
        alice,
        fake,
        "send me a report",
        update_id=chat_id * 10 + 1,
        handle=handle,
        chat_id=chat_id,
    )

    asked = await eventually(
        lambda: _replies(fake, chat_id),
        lambda messages: any(message.native_choices for message in messages),
        describe="the question to arrive with native buttons",
        timeout=60.0,
    )

    offered = {label for message in asked for label in message.native_choices}
    # Matched by containment, not equality: the product decorates a recommended
    # choice ("⭐ Weekly summary") and adds a free-text escape hatch of its own.
    # Both are the product being helpful, and neither changes the choice.
    for choice in ("Weekly summary", "Full ledger"):
        assert any(choice in label for label in offered), (
            f"the agent offered {choice!r} and Telegram was not sent it; "
            f"the buttons were {offered or 'absent entirely'}"
        )
    assert any("type a reply" in label.lower() for label in offered), (
        f"a person must be able to answer in their own words as well as by "
        f"pressing a button; the buttons were {offered}"
    )


@scenario("An agent's approval request reaches the person as native buttons")
@proves("PS-SURF-021", "PS-AGENT-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_an_approval_is_offered_with_native_controls(reachable_pod):
    needs(MODEL_IS_REAL)
    alice, pod, fake, handle, chat_id, table = reachable_pod
    conversation = await _their_conversation(alice, pod, fake, handle, chat_id)

    del conversation  # the surface owns the thread; this scenario just talks
    # A change, not a read. This agent holds no grant that lets it write, so the
    # product refuses and raises the approval itself — which is more reliable
    # than asking the agent nicely to request one, and is the path a person
    # actually walks into.
    await _says_on_telegram(
        alice,
        fake,
        f"add a row titled 'hello' to the {table['name']} table",
        update_id=chat_id * 10 + 2,
        handle=handle,
        chat_id=chat_id,
    )

    offered = await eventually(
        lambda: _replies(fake, chat_id),
        lambda messages: any(message.native_choices for message in messages),
        describe="the approval to arrive with native buttons",
        timeout=60.0,
    )

    labels = " ".join(
        label for message in offered for label in message.native_choices
    ).lower()
    assert "approv" in labels or "allow" in labels or "yes" in labels, (
        f"an approval reached Telegram without an approve control: {labels!r}"
    )
    assert "den" in labels or "reject" in labels or "no" in labels, (
        f"an approval reached Telegram without a deny control: {labels!r}"
    )
