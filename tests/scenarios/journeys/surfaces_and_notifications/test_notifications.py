"""Surfaces and notifications → being told when something needs you."""

from __future__ import annotations

import pytest


from harness import capability, covers, journey, proves, scenario

pytestmark = [
    journey("Surfaces and notifications"),
    capability("Be told when something needs you"),
]


@pytest.fixture
async def team(world):
    alice = await world.new_person("alice")
    organization = await alice.creates_an_organization()
    pod = await alice.creates_a_pod()
    bob = await world.new_person("bob")
    await bob.accepts(await alice.invites(bob, to=organization))
    await alice.adds(bob, to_pod=pod, as_role="POD_USER")
    return alice, bob, pod


@scenario("A person has one place to see what needs them")
@proves("PS-SURF-030")
@covers("notification.send", "notification.list", "notification.unread_count")
async def test_a_notification_arrives_in_the_inbox(team):
    alice, bob, pod = team

    await alice.notifies(bob, in_pod=pod, title="Review the Q3 numbers")

    inbox = await bob.notifications_in(pod)
    assert any(n["title"] == "Review the Q3 numbers" for n in inbox), inbox
    assert await bob.unread_count_in(pod) >= 1


@scenario("Reading a notification clears it from the unread count")
@proves("PS-SURF-031")
@covers("notification.mark_read", "notification.unread_count")
async def test_reading_clears_the_unread_count(team):
    alice, bob, pod = team
    await alice.notifies(bob, in_pod=pod, title="Please look")
    notification = (await bob.notifications_in(pod))[0]
    before = await bob.unread_count_in(pod)

    await bob.reads(notification, in_pod=pod)

    assert await bob.unread_count_in(pod) == before - 1


@scenario("Marking everything read clears the whole inbox")
@proves("PS-SURF-031")
@covers("notification.mark_all_read", "notification.unread_count")
async def test_read_all_clears_everything(team):
    alice, bob, pod = team
    for n in range(3):
        await alice.notifies(bob, in_pod=pod, title=f"Item {n}")

    await bob.reads_everything_in(pod)

    assert await bob.unread_count_in(pod) == 0


@scenario("Read state is per person, not shared")
@proves("PS-SURF-031")
@covers("notification.mark_all_read", "notification.unread_count")
async def test_read_state_is_personal(world, team):
    alice, bob, pod = team
    carol = await world.new_person("carol")
    await carol.accepts(await alice.invites(carol, to=alice.organization))
    await alice.adds(carol, to_pod=pod, as_role="POD_USER")

    await alice.notifies(bob, in_pod=pod, title="For Bob")
    await alice.notifies(carol, in_pod=pod, title="For Carol")

    await bob.reads_everything_in(pod)

    assert await bob.unread_count_in(pod) == 0
    assert await carol.unread_count_in(pod) >= 1, (
        "one person clearing their inbox must not clear another's"
    )


@scenario("A person answers a notification that asked them something")
@proves("PS-SURF-032")
@covers("notification.respond", "notification.list")
async def test_a_notification_can_be_answered(team):
    alice, bob, pod = team
    await alice.notifies(
        bob, in_pod=pod, title="Approve the spend?", expects_response=True
    )
    notification = (await bob.notifications_in(pod))[0]

    await bob.answers(notification, saying="Approved", in_pod=pod)

    answered = await bob.notifications_in(pod)
    assert any(str(n["id"]) == str(notification["id"]) for n in answered)


@scenario("A person acknowledges a notification and it stops asking")
@proves("PS-SURF-032")
@covers("notification.acknowledge")
async def test_a_notification_can_be_acknowledged(team):
    alice, bob, pod = team
    await alice.notifies(bob, in_pod=pod, title="Heads up")
    notification = (await bob.notifications_in(pod))[0]

    await bob.acknowledges(notification, in_pod=pod)


@scenario("Someone outside the pod sees none of its notifications")
@proves("PS-SURF-030")
@covers("notification.list")
async def test_an_outsider_sees_no_notifications(world, team):
    alice, bob, pod = team
    await alice.notifies(bob, in_pod=pod, title="Internal")

    outsider = await world.new_person("outsider")

    response = await outsider.api.call("GET", f"/pods/{pod['id']}/notifications")
    assert response.status_code >= 400, (
        f"an outsider read pod notifications ({response.status_code})"
    )


@scenario("A removed member cannot act on the notifications they still hold")
@proves("PS-SURF-030")
@covers("notification.respond", "notification.acknowledge", "notification.mark_read")
async def test_removal_closes_the_inbox_it_left_behind(world, team):
    """Reading was gated; answering was not, and answering is the louder half.

    A notification is how an agent asks a person something, so responding to
    one steers the run that asked. Gating the two read routes and leaving
    respond/acknowledge/mark_read open closed the window and left the door.
    Same rule as PS-POD-040: membership is the precondition, for reading and
    for acting alike.
    """
    alice, bob, pod = team
    await alice.notifies(bob, in_pod=pod, title="Ship it?")
    notification = (await bob.notifications_in(pod))[0]

    await alice.removes_member(await alice.membership_of(bob, in_pod=pod), from_pod=pod)

    answered = await bob.api.call(
        "POST",
        f"/pods/{pod['id']}/notifications/{notification['id']}/respond",
        json={"summary": "Yes, ship it."},
    )
    dismissed = await bob.api.call(
        "POST",
        f"/pods/{pod['id']}/notifications/{notification['id']}/acknowledge",
    )
    read = await bob.api.call(
        "POST",
        f"/pods/{pod['id']}/notifications/{notification['id']}/read",
    )

    # All three reported together: which of them is still open says how much of
    # the inbox a removed person keeps, and stopping at the first would hide it.
    assert (
        answered.status_code >= 400
        and dismissed.status_code >= 400
        and read.status_code >= 400
    ), (
        f"a removed member could still act on their old notifications: "
        f"respond={answered.status_code} acknowledge={dismissed.status_code} "
        f"mark_read={read.status_code}"
    )
