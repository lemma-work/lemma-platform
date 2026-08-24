"""Surfaces and notifications → threads, and things sent along with a message.

Two ways a surface is more than a message pipe. A platform's threads have to
line up with the pod's conversations, or a person's second message arrives with
no memory of their first. And a file sent to the bot has to reach the agent, or
"send me that spreadsheet" is a conversation the product cannot have.
"""

from __future__ import annotations

from harness import capability, covers, journey, proves, scenario
from harness.fake_platform import FILE_CONTENTS
from harness.waiting import eventually

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Receive a message from outside"),
]


async def _threads_in(reachable):
    return await reachable.alice.conversations_in(reachable.pod)


@scenario("Two messages in one chat continue one conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list", "agent.conversation.get")
async def test_a_chat_is_one_conversation(reachable):
    await reachable.says("first thing", update_id=101)
    await reachable.waits_for_a_reply()

    await reachable.says("and a second thing", update_id=102)
    await reachable.waits_for_a_reply(after=1)

    threads = await _threads_in(reachable)
    assert len(threads) == 1, (
        "a second message in the same chat started a second conversation, so "
        f"the agent answers it having forgotten the first: {threads}"
    )

    # And both messages are in it, which is what "the same conversation" has to
    # mean — a thread that exists but lost the first message is no better.
    said = await reachable.alice.messages_in(threads[0], in_pod=reachable.pod)
    spoken = " ".join(str(message.get("text") or "") for message in said)
    assert "first thing" in spoken and "and a second thing" in spoken, (
        f"the conversation does not hold both messages: {spoken[:400]}"
    )


@scenario("A different chat is a different conversation")
@proves("PS-SURF-013")
@covers("agent.conversation.list")
async def test_a_separate_chat_is_a_separate_conversation(reachable):
    other_chat = reachable.chat_id + 1

    await reachable.says("over here", update_id=111)
    await reachable.waits_for_a_reply()
    await reachable.says("and over there", update_id=112, chat_id=other_chat)
    await reachable.waits_for_a_reply(chat_id=other_chat)

    threads = await eventually(
        lambda: _threads_in(reachable),
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
    await reachable.says("hello from outside", update_id=121)
    await reachable.waits_for_a_reply()

    [thread] = await _threads_in(reachable)
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
    await reachable.says(
        "here is the spreadsheet",
        update_id=131,
        document={
            "file_id": "scenarios-doc-1",
            "file_unique_id": "scenarios-doc-1",
            "file_name": "quarter.csv",
            "mime_type": "text/csv",
            "file_size": len(FILE_CONTENTS),
        },
    )

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
    # finds nothing in it.
    assert (
        await reachable.alice.downloads(landed, in_pod=reachable.pod) == FILE_CONTENTS
    ), f"the file at {landed} does not contain what was sent"
