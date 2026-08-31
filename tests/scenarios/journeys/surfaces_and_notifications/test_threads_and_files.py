"""Surfaces and notifications → threads, and things sent along with a message.

Two ways a surface is more than a message pipe. A platform's threads have to
line up with the pod's conversations, or a person's second message arrives with
no memory of their first. And a file sent to the bot has to reach the agent, or
"send me that spreadsheet" is a conversation the product cannot have.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario, stack_lane
from harness.waiting import eventually, UNTIL_A_RUN_SETTLES

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]


@scenario("Two messages in one chat continue one conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list", "agent.conversation.get")
async def test_a_chat_is_one_conversation(reachable, run):
    # Named through `run` because a person has one chat with a bot and it stands
    # between runs: last night's "first thing" is still in the conversation, so
    # literal text would let this pass on messages it never sent. The promise is
    # about *these two* messages ending up together.
    first = run.name("first thing")
    second = run.name("and a second thing")

    await reachable.says(first)
    # Wait for the message to land, not for the agent to answer it. Both are
    # true things to want and only one of them is this promise: where a message
    # goes is PS-SURF-013, and whether a model replies inside two minutes is
    # PS-SURF-010, which has its own scenario. Waiting on the reply here made
    # this fail in three runs out of five with "the agent never answered on
    # Telegram" — a sentence about dev's round-trip latency, printed in place of
    # the question the scenario exists to ask, which was never reached.
    await reachable.waits_for_a_conversation_holding(first)

    await reachable.says(second)

    # `waits_for_a_conversation_holding` asserts the "one" itself: more than one
    # conversation carrying these messages is the failure this scenario is for.
    await reachable.waits_for_a_conversation_holding(first, second)


@scenario("A different chat is a different conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list")
@stack_lane("two chats at once only a forged delivery can produce")
async def test_a_separate_chat_is_a_separate_conversation(reachable):
    # A person has exactly one chat with a bot: they cannot be in two places to
    # prove the two are kept apart. Forging the second delivery is the only way
    # to ask this question at all, so this scenario stays in the lane that can.
    reachable.only_forged("two chats at once is something only a platform has")

    elsewhere = reachable.chat.in_another_chat()

    await reachable.says("over here")
    await reachable.waits_for_a_reply()
    await elsewhere.says("and over there")
    await elsewhere.waits_for_a_reply()

    threads = await eventually(
        reachable.conversations,
        lambda found: len(found) >= 2,
        describe="a second chat to get its own conversation",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    assert len(threads) == 2, (
        f"two separate chats share one conversation, so each sees the other's "
        f"history: {threads}"
    )


@scenario("A conversation from a surface says where it came from")
@proves("PS-SURF-013")
@covers("agent.conversation.get")
async def test_a_surface_conversation_records_its_origin(reachable, run):
    greeting = run.name("hello from outside")

    await reachable.says(greeting)

    # By content rather than by novelty. A person has one chat with a bot and it
    # stands between runs, so this message lands in the conversation an earlier
    # run opened — nothing is created, and asking for "the conversation this
    # scenario made" got an empty list and failed unpacking it. Named through
    # `run` for the other half of the same fact: a literal greeting is already
    # in that conversation from last night, so it would be found before this
    # run's message had arrived at all.
    thread = await reachable.waits_for_a_conversation_holding(greeting)
    opened = await reachable.alice.opens_conversation(thread, in_pod=reachable.pod)

    # Somebody reading this in the workspace has to be able to tell it arrived
    # from Telegram rather than from the web, or a reply that mentions "the
    # message you sent" is unattributable.
    trace = str(opened).lower()
    assert "telegram" in trace or "surface" in trace, (
        f"nothing on this conversation says it came from a surface: {opened}"
    )


#: Where a surface saves what somebody sent it. Asking the directory directly is
#: the difference between a listing that pages and a tree that truncates.
TELEGRAM_FOLDER = "/me/telegram"


@scenario("A file sent to the bot reaches the pod, contents and all")
@proves("PS-SURF-014")
@covers("surface.webhook.handle_platform", "file.list", "file.download")
async def test_an_attachment_reaches_the_pod(reachable):
    sent = await reachable.sends_file("quarter.csv", caption="here is the spreadsheet")

    # The paginated listing of the folder the file lands in, not the file tree.
    # The tree returns three files per directory by default and says so only in
    # `has_more_files`, so on a pod that stands between runs it reported this
    # file missing for four consecutive nights while it sat there the whole
    # time — the three older photos above it alphabetically were the whole cap.
    arrived = await eventually(
        lambda: reachable.alice.paths_in(reachable.pod, directory=TELEGRAM_FOLDER),
        lambda found: any(path.endswith("quarter.csv") for path in found),
        describe="the attachment to reach the pod",
        timeout=UNTIL_A_RUN_SETTLES,
    )
    landed = sorted(path for path in arrived if path.endswith("quarter.csv"))[0]

    # The bytes, not just the name. A file recorded but never fetched is the
    # failure this is really about: the agent is told there is a spreadsheet and
    # finds nothing in it. Compared against what was actually sent, because the
    # two lanes cannot send the same thing by accident.
    assert await reachable.alice.downloads(landed, in_pod=reachable.pod) == sent, (
        f"the file at {landed} does not contain what was sent"
    )
