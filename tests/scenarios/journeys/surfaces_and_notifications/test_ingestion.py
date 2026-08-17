"""Surfaces and notifications → a message from outside reaches the agent.

The full path: a person messages the bot on Telegram, Lemma verifies the
delivery is genuine, resolves who they are, runs the agent, and replies in the
same chat.

Telegram itself is stood in for by `harness.fake_platform` — pointed at through
`api_base_url` on the connected account, which the platform supports for
self-hosted Bot API servers. Lemma runs entirely for real.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.fake_platform import start_fake_telegram
from harness.waiting import eventually, never

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]



@pytest.fixture
async def telegram(world):
    """A pod reachable on Telegram, with the platform stood in for."""
    fake = start_fake_telegram()
    try:
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        agent = await alice.creates_an_agent(in_pod=pod)

        auth_config = await alice.installs_connector("telegram", in_organization=organization)
        account = await alice.connects_account(
            in_organization=organization,
            auth_config=auth_config,
            credentials={
                "bot_token": "424242:scenarios",
                # The documented override for a self-hosted Bot API server.
                "api_base_url": fake.api_base,
            },
        )
        surface = await alice.connects_a_surface(
            in_pod=pod,
            platform="TELEGRAM",
            named="tg",
            agent=agent["name"],
            account=account,
        )
        del surface
        fake.clear()
        yield alice, pod, fake
    finally:
        fake.stop()


def _update(*, chat_id: int, text: str, from_id: int, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1700000000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": from_id, "is_bot": False, "first_name": "Sender"},
            "text": text,
        },
    }


@scenario("A message from an unrecognised sender is answered with how to get access")
@proves("PS-SURF-010", "PS-SURF-012", "PS-SURF-020")
@covers("surface.webhook.handle_platform", "agent.surface.send", "surface.message_answered")
async def test_an_unknown_sender_is_told_how_to_get_access(world, telegram):
    alice, pod, fake = telegram
    chat_id = 55501

    # Delivered where Lemma told Telegram to deliver, not to a guessed path.
    delivered = await alice.api.call(
        "POST", fake.webhook_path,
        json=_update(chat_id=chat_id, text="hello there", from_id=chat_id),
        headers={"X-Telegram-Bot-Api-Secret-Token": fake.webhook_secret},
    )
    assert delivered.status_code < 400, (
        f"a correctly signed delivery was rejected: {delivered.status_code} "
        f"{delivered.text[:300]}"
    )

    replies = await eventually(
        lambda: _sent(fake, chat_id),
        lambda messages: bool(messages),
        describe="the agent to reply in the Telegram chat",
        timeout=60.0,
    )

    answer = replies[0].text
    assert answer, f"a reply with no words is not an answer: {replies[0].payload}"
    # This sender is a Telegram id nobody has linked to a Lemma account, so the
    # right answer is how to become known — not pod content, and not silence.
    assert "link" in answer.lower() or "account" in answer.lower(), (
        f"an unrecognised sender should be told how to get access; got: {answer!r}"
    )
    # And it is asked natively, not as text telling them what to type.
    assert replies[0].native_choices, (
        "Telegram supports native controls, so the ask should use one: "
        f"{replies[0].payload}"
    )


@scenario("A delivery without the platform's secret is rejected")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_an_unsigned_delivery_is_rejected(world, telegram):
    alice, pod, fake = telegram
    chat_id = 55502

    delivered = await alice.api.call(
        "POST", fake.webhook_path,
        json=_update(chat_id=chat_id, text="let me in", from_id=chat_id),
    )

    assert delivered.status_code >= 400, (
        f"an unsigned delivery was accepted ({delivered.status_code})"
    )
    await never(
        lambda: _sent(fake, chat_id),
        lambda messages: bool(messages),
        describe="an answer to an unsigned delivery",
        within=3.0,
    )


@scenario("A delivery with the wrong secret is rejected")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_a_wrongly_signed_delivery_is_rejected(world, telegram):
    alice, pod, fake = telegram
    chat_id = 55503

    delivered = await alice.api.call(
        "POST", fake.webhook_path,
        json=_update(chat_id=chat_id, text="let me in", from_id=chat_id),
        headers={"X-Telegram-Bot-Api-Secret-Token": "not-the-secret"},
    )

    assert delivered.status_code >= 400, (
        f"a delivery with the wrong secret was accepted ({delivered.status_code})"
    )


@scenario("The same delivery twice is answered once")
@proves("PS-SURF-011")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_repeated_delivery_is_answered_once(world, telegram):
    alice, pod, fake = telegram
    chat_id = 55504
    update = _update(chat_id=chat_id, text="only once please", from_id=chat_id, update_id=9001)
    headers = {"X-Telegram-Bot-Api-Secret-Token": fake.webhook_secret}

    await alice.api.call("POST", fake.webhook_path, json=update, headers=headers)
    await alice.api.call("POST", fake.webhook_path, json=update, headers=headers)

    await eventually(
        lambda: _sent(fake, chat_id),
        lambda messages: bool(messages),
        describe="the agent to reply at least once",
        timeout=60.0,
    )
    # Give a duplicate every chance to produce a second answer before claiming
    # it did not.
    await never(
        lambda: _sent(fake, chat_id),
        lambda messages: len(messages) > 1,
        describe="a second answer to the same delivery",
        within=6.0,
    )


async def _sent(fake, chat_id: int):
    """The replies the platform has received for one chat.

    A plain function rather than a lambda so the waiters can call it: they take
    an awaitable, and this has to look like one.
    """
    return fake.messages_to(chat_id)
