"""Surfaces and notifications → a delivery Telegram would never have made.

Everything here is about what Lemma does with a delivery it should not simply
believe: one that is unsigned, one signed with the wrong secret, one it has
already handled, two of the same racing each other, and one from somebody it
has never heard of.

That is why these take the forged lane and say so. A real account cannot send a
message twice with the same update id, cannot omit the signature, and cannot be
a stranger and a colleague at once — those are things only the platform does,
and a webhook nobody can route to from the internet is only as safe as its
refusal to believe whatever arrives at it.

The full happy path — somebody messages the bot and is answered in the same
chat — is `test_being_answered.py`, and it runs against a real account where
there is one.
"""

from __future__ import annotations

import asyncio

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually, never

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]

#: This waits on a queued agent run, and the whole suite shares one worker. CI
#: shards by journey so the worker is never this loaded there; a local run of
#: all 350 at once is the harsh case, and a wait costs nothing when things are
#: fast. 60s and 120s both timed out under it.
UNTIL_ANSWERED = 240.0


@pytest.mark.timeout(360)
@scenario("A message from an unrecognised sender is answered with how to get access")
@proves("PS-SURF-010", "PS-SURF-012", "PS-SURF-020")
@covers(
    "surface.webhook.handle_platform", "agent.surface.send", "surface.message_answered"
)
async def test_an_unknown_sender_is_told_how_to_get_access(forged):
    stranger = forged.chat.as_a_stranger()

    await stranger.says("hello there")

    replies = await eventually(
        stranger.replies,
        bool,
        describe="the agent to reply in the Telegram chat",
        timeout=UNTIL_ANSWERED,
    )

    answer = replies[0].text
    assert answer, "a reply with no words is not an answer"
    # This sender is a Telegram account nobody has linked to a Lemma user, so
    # the right answer is how to become known — not pod content, and not silence.
    assert "link" in answer.lower() or "account" in answer.lower(), (
        f"an unrecognised sender should be told how to get access; got: {answer!r}"
    )
    # And it is asked natively, not as text telling them what to type.
    assert replies[0].choices, (
        f"Telegram supports native controls, so the ask should use one: {answer!r}"
    )


@scenario("A delivery without the platform's secret is rejected")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_an_unsigned_delivery_is_rejected(forged):
    chat = forged.chat

    delivered = await chat.delivers(chat.update("let me in"), signed=False)

    assert delivered.status_code >= 400, (
        f"an unsigned delivery was accepted ({delivered.status_code})"
    )
    await never(
        chat.replies,
        bool,
        describe="an answer to an unsigned delivery",
        within=3.0,
    )


@scenario("A delivery with the wrong secret is rejected")
@proves("PS-SURF-010")
@covers("surface.webhook.handle_platform")
async def test_a_wrongly_signed_delivery_is_rejected(forged):
    chat = forged.chat

    delivered = await chat.delivers(chat.update("let me in"), secret="not-the-secret")

    assert delivered.status_code >= 400, (
        f"a delivery with the wrong secret was accepted ({delivered.status_code})"
    )
    await never(
        chat.replies,
        bool,
        describe="an answer to a wrongly signed delivery",
        within=3.0,
    )


@scenario("The same delivery twice is answered once")
@proves("PS-SURF-011", "PS-SCHED-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_repeated_delivery_is_answered_once(forged):
    chat = forged.chat
    # The same update object both times: an update id already handled is what
    # makes the second one a duplicate rather than a second message.
    twice = chat.update("only once please")

    await chat.delivers(twice)
    await chat.delivers(twice)

    await eventually(
        chat.replies,
        bool,
        describe="the agent to reply at least once",
        timeout=UNTIL_ANSWERED,
    )
    # Give a duplicate every chance to produce a second answer before claiming
    # it did not.
    await never(
        chat.replies,
        lambda messages: len(messages) > 1,
        describe="a second answer to the same delivery",
        within=6.0,
    )


@scenario("Two deliveries of one trigger racing each other still do the work once")
@proves("PS-SURF-011", "PS-SCHED-020")
@covers("surface.webhook.handle_platform", "agent.surface.send")
async def test_a_raced_delivery_is_answered_once(forged):
    chat = forged.chat
    at_once = chat.update("exactly once")

    # Sent together rather than one after the other. A platform retrying on
    # timeout does not wait for the first attempt to finish, so sequential
    # delivery tests the easy half — the second arriving when the first is
    # already recorded. This tests the half that needs a lock.
    await asyncio.gather(chat.delivers(at_once), chat.delivers(at_once))

    await eventually(
        chat.replies,
        bool,
        describe="the agent to reply at least once",
        timeout=UNTIL_ANSWERED,
    )
    await never(
        chat.replies,
        lambda messages: len(messages) > 1,
        describe="a second answer to two racing deliveries of one update",
        within=6.0,
    )
    # PS-SCHED-020 also promises this holds across a restart of any component
    # involved. That is not exercised here — restarting the worker mid-delivery
    # does not belong in a suite that runs on every change.
