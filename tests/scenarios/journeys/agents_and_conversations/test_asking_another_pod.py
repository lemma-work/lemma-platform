"""Agents and conversations → one pod's assistant asking another's.

Two ways in. A person in both pods lets their assistant ask the other as them,
with their access there. An admin of a pod can instead connect another pod to
it, and that pod may then ask with nobody present -- reading only what is Public
plus what the admin chose to share.

Which pods can be asked, and how, is the part a person sees and decides, so
most of what is proved here needs no model. Asking itself does.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.credentials import needs
from harness.environment import MODEL_IS_REAL
from harness.steps.datastore import column
from harness.steps.pod import shared_table
from harness.waiting import UNTIL_A_MODEL_ACTS, eventually

pytestmark = [journey("Agents and conversations"), capability("Ask another pod")]


@pytest.fixture
async def two_pods(world, run):
    """Two pods priya administers: one that asks, and one that answers."""
    priya = await world.person("priya")
    asking = await priya.creates_a_pod(named=run.name("asking"))
    answering = await priya.creates_a_pod(named=run.name("answering"))
    try:
        yield priya, asking, answering
    finally:
        await priya.deletes_pod(asking)
        await priya.deletes_pod(answering)


def _listed(pods: list[dict], pod: dict) -> dict | None:
    return next((one for one in pods if str(one.get("pod_id")) == str(pod["id"])), None)


@scenario("A person sees the other pods a pod can ask, and how it asks each")
@proves("PS-AGENT-043")
@covers("agent.askable_pod.list", "agent.pod_link.connect", "pod.member.add")
async def test_a_pod_lists_only_the_pods_it_can_ask(world, two_pods):
    priya, asking, answering = two_pods
    sofia = await world.person("sofia")
    await priya.adds(sofia, to_pod=asking, as_role="POD_VIEWER")

    # priya is in both, so the asking pod can ask the other through her.
    seen = _listed(await priya.pods_it_can_ask(asking), answering)
    assert seen is not None, "a pod priya is in was not listed as one she can ask"
    assert seen["through_you"] is True and seen["connected"] is False, seen

    # sofia is not in the answering pod and nothing connects the two yet, so
    # for her it is not there at all -- not listed as something she cannot use.
    assert _listed(await sofia.pods_it_can_ask(asking), answering) is None, (
        "a pod sofia is not in, and that is not connected, was listed to her"
    )

    await priya.connects(asking, to_pod=answering)

    connected = _listed(await sofia.pods_it_can_ask(asking), answering)
    assert connected is not None, "a connected pod was not listed"
    assert connected["connected"] is True and connected["through_you"] is False, connected
    both = _listed(await priya.pods_it_can_ask(asking), answering)
    assert both is not None and both["through_you"] and both["connected"], both


@scenario("An admin connects another pod, and sees what it reads and who connected it")
@proves("PS-ACCESS-045")
@covers("agent.pod_link.connect", "agent.pod_link.list", "table.create")
async def test_an_admin_connects_a_pod_sharing_one_table(world, two_pods):
    priya, asking, answering = two_pods
    table = await priya.creates_a_table(in_pod=answering, columns=[column("title")])

    await priya.connects(asking, to_pod=answering, sharing=[shared_table(table["name"])])

    link = _listed(await priya.pods_that_can_ask(answering), asking)
    assert link is not None, "the connected pod is not listed as able to ask"
    assert str(link["steward_user_id"]) == str(priya.user_id), link
    assert [
        (one["resource_name"], sorted(one["permission_ids"])) for one in link["shared"]
    ] == [(table["name"], ["datastore.record.read", "datastore.table.read"])], link[
        "shared"
    ]

    # Connecting again replaces what is shared; it never adds to it unseen.
    await priya.connects(asking, to_pod=answering)
    again = _listed(await priya.pods_that_can_ask(answering), asking)
    assert again is not None and again["shared"] == [], again


@scenario("A connection shares for reading only, and never somebody's own table")
@proves("PS-ACCESS-045")
@covers("agent.pod_link.connect", "agent.pod_link.list")
async def test_a_connection_refuses_to_share_more_than_reading(world, two_pods):
    priya, asking, answering = two_pods
    table = await priya.creates_a_table(in_pod=answering, columns=[column("title")])
    own = await priya.creates_a_table(
        in_pod=answering, columns=[column("title")], visibility="PERSONAL"
    )

    writing = {
        **shared_table(table["name"]),
        "permission_ids": ["datastore.record.create"],
    }
    assert await priya.is_refused_connecting(
        asking, to_pod=answering, sharing=[writing]
    ) == (400, "POD_LINK_READ_ONLY")

    assert await priya.is_refused_connecting(
        asking, to_pod=answering, sharing=[shared_table(own["name"])]
    ) == (400, "POD_LINK_PERSONAL")

    assert _listed(await priya.pods_that_can_ask(answering), asking) is None, (
        "a refused connection was made anyway"
    )


@scenario("Only an admin who is also in the other pod can connect it")
@proves("PS-ACCESS-045")
@covers("agent.pod_link.connect", "agent.pod_link.disconnect", "pod.member.add")
async def test_only_an_admin_in_both_pods_can_connect(world, run, two_pods):
    priya, asking, answering = two_pods

    # sofia is in both pods, so only not administering the answering one stops her.
    sofia = await world.person("sofia")
    await priya.adds(sofia, to_pod=asking, as_role="POD_VIEWER")
    await priya.adds(sofia, to_pod=answering, as_role="POD_VIEWER")
    status, _ = await sofia.is_refused_connecting(asking, to_pod=answering)
    assert status == 403, status

    # priya administers the answering pod, but is not in wei's.
    wei = await world.person("wei")
    elsewhere = await wei.creates_a_pod(named=run.name("elsewhere"))
    try:
        status, _ = await priya.is_refused_connecting(elsewhere, to_pod=answering)
        assert status == 409, status
    finally:
        await wei.deletes_pod(elsewhere)

    await priya.connects(asking, to_pod=answering)
    assert await sofia.is_refused_disconnecting(asking, from_pod=answering) == 403
    assert _listed(await priya.pods_that_can_ask(answering), asking) is not None, (
        "a refused disconnect took the connection away"
    )


@scenario("Disconnecting a pod takes back what it was shared, and it can no longer ask")
@proves("PS-ACCESS-045")
@covers(
    "agent.pod_link.connect",
    "agent.pod_link.disconnect",
    "agent.pod_link.list",
    "agent.askable_pod.list",
)
async def test_disconnecting_ends_the_connection(world, two_pods):
    priya, asking, answering = two_pods
    sofia = await world.person("sofia")
    await priya.adds(sofia, to_pod=asking, as_role="POD_VIEWER")
    table = await priya.creates_a_table(in_pod=answering, columns=[column("title")])
    await priya.connects(asking, to_pod=answering, sharing=[shared_table(table["name"])])
    assert _listed(await sofia.pods_it_can_ask(asking), answering) is not None

    await priya.disconnects(asking, from_pod=answering)

    assert _listed(await priya.pods_that_can_ask(answering), asking) is None, (
        "the pod still reads what it was shared after being disconnected"
    )
    assert _listed(await sofia.pods_it_can_ask(asking), answering) is None, (
        "a disconnected pod is still listed as one this pod can ask"
    )
    assert await priya.is_refused_disconnecting(asking, from_pod=answering) == 404


@scenario("A pod's assistant asks another pod as the person, and brings the answer back")
@proves("PS-AGENT-042")
@covers(
    "agent.conversation.get",
    "agent.conversation.approval.resolve",
    "agent.conversation.list",
)
async def test_an_assistant_asks_another_pod_as_the_person(world, run, two_pods):
    needs(MODEL_IS_REAL)
    priya, asking, answering = two_pods
    code_word = run.name("heron")
    table = await priya.creates_a_table(in_pod=answering, columns=[column("title")])
    await priya.adds_record(
        {"title": code_word}, to_table=table["name"], in_pod=answering
    )
    # Only the pod's own assistant may ask another pod; a named agent is
    # refused. So this talks to the assistant, as a person in Lemma would.
    conversation = await priya.starts_a_conversation(
        watching=False,
        in_pod=asking,
        saying=(
            f"Ask the pod {answering['name']!r} what title is written in its "
            f"table {table['name']!r}, and tell me exactly what it answered."
        ),
    )
    # The first ask to a pod in a conversation waits for her OK.
    asked = await priya.answers_every_approval(conversation, allow=True, in_pod=asking)
    # Without the call itself, no approval could mean the ask was refused or
    # never attempted -- neither of which is "asked without permission".
    messages = await priya.messages_in(conversation, in_pod=asking)
    asks = [message for message in messages if message.get("tool_name") == "ask_teammate"]
    assert asks, (
        "the assistant never tried to ask the other pod: "
        f"{[message.get('tool_name') for message in messages]}"
    )
    # A refused ask also stops nobody for approval; its result says why.
    assert asked >= 1, (
        "the assistant asked another pod without asking priya first: "
        f"{[message.get('tool_result') for message in asks]}"
    )

    await eventually(
        lambda: priya.transcript_of(conversation, in_pod=asking),
        lambda transcript: code_word in transcript,
        describe="the other pod's answer to arrive in the conversation that asked",
        timeout=UNTIL_A_MODEL_ACTS,
    )
    # The other pod answered in a thread of priya's own there, with her access.
    assert await priya.conversations_in(answering), (
        "the other pod answered without a conversation of priya's own there"
    )
