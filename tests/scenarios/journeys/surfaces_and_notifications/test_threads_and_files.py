"""Surfaces and notifications → threads, and things sent along with a message.

Two ways a surface is more than a message pipe. A platform's threads have to
line up with the pod's conversations, or a person's second message arrives with
no memory of their first. And a file sent to the bot has to reach the agent, or
"send me that spreadsheet" is a conversation the product cannot have.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]


@scenario("Two messages in one chat continue one conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list", "agent.conversation.get")
async def test_a_chat_is_one_conversation(reachable):
    await reachable.says("first thing")
    await reachable.waits_for_a_reply()

    await reachable.says("and a second thing")

    # Asked of the pod, not counted off the chat. Whether the agent answered
    # twice is incidental to this promise — what it is about is that the second
    # message joins the first rather than starting somewhere new. Waiting on a
    # second reply made this fail against a real deployment for a reason it was
    # not testing, and would have gone on saying "the agent never answered"
    # about a product that had put both messages exactly where they belong.
    #
    # `waits_for_a_conversation_holding` asserts the "one" itself: more than one
    # conversation carrying these messages is the failure this scenario is for.
    await reachable.waits_for_a_conversation_holding("first thing", "and a second thing")


@scenario("A different chat is a different conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list")
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
        timeout=60.0,
    )
    assert len(threads) == 2, (
        f"two separate chats share one conversation, so each sees the other's "
        f"history: {threads}"
    )


@scenario("A conversation from a surface says where it came from")
@proves("PS-SURF-013")
@covers("agent.conversation.get")
async def test_a_surface_conversation_records_its_origin(reachable):
    await reachable.says("hello from outside")

    # By content rather than by novelty. A person has one chat with a bot and it
    # stands between runs, so this message lands in the conversation an earlier
    # run opened — nothing is created, and asking for "the conversation this
    # scenario made" got an empty list and failed unpacking it.
    thread = await reachable.waits_for_a_conversation_holding("hello from outside")
    opened = await reachable.alice.opens_conversation(thread, in_pod=reachable.pod)

    # Somebody reading this in the workspace has to be able to tell it arrived
    # from Telegram rather than from the web, or a reply that mentions "the
    # message you sent" is unattributable.
    trace = str(opened).lower()
    assert "telegram" in trace or "surface" in trace, (
        f"nothing on this conversation says it came from a surface: {opened}"
    )


def _paths_in(tree) -> set[str]:
    """Every file path in a pod, flattened.

    The file *list* is the root directory only, so an attachment landing in a
    folder is invisible to it — which is how a working feature reads as broken.
    Ingested files go to the sender's own folder, so the tree is where to look.
    """
    found: set[str] = set()

    def walk(node) -> None:
        if not isinstance(node, dict):
            return
        if str(node.get("kind")) == "FILE":
            found.add(str(node.get("path")))
        for child in node.get("children") or []:
            walk(child)

    walk((tree or {}).get("tree"))
    return found


@scenario("A file sent to the bot reaches the pod, contents and all")
@proves("PS-SURF-014")
@covers("surface.webhook.handle_platform", "file.tree", "file.download")
async def test_an_attachment_reaches_the_pod(reachable):
    sent = await reachable.sends_file("quarter.csv", caption="here is the spreadsheet")

    async def paths():
        return _paths_in(await reachable.alice.file_tree_of(reachable.pod))

    arrived = await eventually(
        paths,
        lambda found: any(path.endswith("quarter.csv") for path in found),
        describe="the attachment to reach the pod",
        timeout=90.0,
    )
    [landed] = [path for path in arrived if path.endswith("quarter.csv")]

    # The bytes, not just the name. A file recorded but never fetched is the
    # failure this is really about: the agent is told there is a spreadsheet and
    # finds nothing in it. Compared against what was actually sent, because the
    # two lanes cannot send the same thing by accident.
    assert await reachable.alice.downloads(landed, in_pod=reachable.pod) == sent, (
        f"the file at {landed} does not contain what was sent"
    )
