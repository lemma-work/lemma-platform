"""Surfaces and notifications → a delivery Telegram would never have made.

Everything here is about what Lemma does with a delivery it should not simply
believe: one that is unsigned, one signed with the wrong secret, one it has
already handled, two of the same racing each other, one from somebody it has
never heard of, and one from somebody it knows perfectly well who has no place
in the pod on the other side.

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
from uuid import uuid4

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

    # Same reason as the scenario below: the streamed placeholder satisfies
    # "there is a reply" before there are any words in it.
    spoken = await stranger.waits_for_a_reply(timeout=UNTIL_ANSWERED)

    answer = spoken.text
    assert answer, "a reply with no words is not an answer"
    # This sender is a Telegram account nobody has linked to a Lemma user, so
    # the right answer is how to become known — not pod content, and not silence.
    assert "link" in answer.lower() or "account" in answer.lower(), (
        f"an unrecognised sender should be told how to get access; got: {answer!r}"
    )
    # And it is asked natively, not as text telling them what to type.
    assert spoken.choices, (
        f"Telegram supports native controls, so the ask should use one: {answer!r}"
    )


@pytest.mark.timeout(360)
@scenario("Somebody with an account but no place in the pod is not let into it")
@proves("PS-SURF-012")
@covers("surface.webhook.handle_platform", "agent.surface.send", "pod.get")
async def test_reaching_the_bot_is_not_membership_of_the_pod(world, forged):
    """The sender this promise is really about, and the easiest one to miss.

    A stranger is stopped by the identity check, which the scenario above
    proves. This one passes that check: they have a Lemma account and an
    `@username` Lemma resolves, so every question about who they are answers
    cleanly. The only thing between them and the pod is that nobody put them in
    it — and a resolved sender looks in every way like a member until something
    actually asks.

    That is the boundary the specification insists on, in its own words: being
    present on the platform "shall not by itself grant access to the pod". So
    both halves of what it says happens instead are asked for here — they are
    told how to get access, and the pod is still shut to them afterwards.

    Forged, and it has to be. A Telegram account belongs to one person
    deployment-wide, so a real one cannot be the member and the outsider in the
    same run — the same reason the rest of this file takes the lane it does.
    """
    outsider = await world.new_person("hannah")
    # Unique per run, because a handle is claimed deployment-wide and the claim
    # is sticky on purpose: reusing one would resolve this message to whoever an
    # earlier run bound it to.
    handle = f"hannah_{uuid4().hex[:10]}"
    await outsider.is_known_on_telegram_as(handle)
    theirs = forged.chat.as_another_person(handle)

    await theirs.says("Show me what is in this workspace.")

    # `waits_for_a_reply`, not `replies` — Lemma streams, so the first message
    # in a chat is an empty placeholder it fills in as the answer arrives, and
    # waiting for "a message" would hand this assertion `''`.
    answer = (await theirs.waits_for_a_reply(timeout=UNTIL_ANSWERED)).text
    assert "access" in answer.lower(), (
        f"a signed-up person outside the pod should be told how to get into it "
        f"rather than left guessing why the bot went quiet; got: {answer!r}"
    )
    # Nothing was started on their behalf. The routing decision that produced
    # that answer is the one that would otherwise have opened a conversation, so
    # by the time the answer is here, either there is one or there never will be.
    assert await forged.conversations() == [], (
        "a message from somebody outside the pod opened a conversation inside "
        "it, which is an agent running with pod tools for a person who has no "
        "claim on them"
    )
    # And the refusal was about this pod, not about this platform: reaching the
    # bot bought them nothing they did not already have.
    await outsider.is_refused_pod(forged.pod)


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
