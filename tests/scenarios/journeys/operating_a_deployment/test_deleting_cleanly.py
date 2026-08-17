"""Operating a deployment → deleting a pod really stops it.

Deletion is the one operation whose failure is invisible from the inside. A pod
that is gone from every list can still be holding a webhook registration on
somebody else's server and a timer in the scheduler — and the only symptom is
work happening for a pod nobody can look at any more.

So these scenarios check the outside of the system, not the listing: they ask
the platform Lemma registered with whether it was told to stop.
"""

from __future__ import annotations

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.fake_platform import start_fake_telegram
from harness.waiting import never

pytestmark = [
    journey("Operating a deployment"),
    capability("Delete cleanly"),
]


@pytest.fixture
async def pod_doing_things(world):
    """A pod with standing work: a surface listening and a schedule waiting."""
    fake = start_fake_telegram()
    try:
        alice = await world.new_person("alice")
        organization = await alice.creates_an_organization()
        pod = await alice.creates_a_pod()
        agent = await alice.creates_an_agent(in_pod=pod)

        auth_config = await alice.installs_connector(
            "telegram", in_organization=organization
        )
        account = await alice.connects_account(
            in_organization=organization,
            auth_config=auth_config,
            credentials={
                "bot_token": "424242:scenarios",
                "api_base_url": fake.api_base,
            },
        )
        await alice.connects_a_surface(
            in_pod=pod,
            platform="TELEGRAM",
            named="tg",
            agent=agent["name"],
            account=account,
        )
        # Every quarter hour, which is as often as the product allows.
        schedule = await alice.creates_a_schedule(
            in_pod=pod, agent=agent["name"], config={"cron": "*/15 * * * *"}
        )
        # Keep the webhook registration; forget the setup traffic.
        webhook_path, webhook_secret = fake.webhook_path, fake.webhook_secret
        fake.clear()
        yield alice, pod, schedule, fake, webhook_path, webhook_secret
    finally:
        fake.stop()


@scenario("A deleted pod stops answering on the surfaces it was reachable on")
@proves("PS-OPS-020")
@covers("pod.delete", "surface.webhook.handle_platform", "pod.deleted")
async def test_a_deleted_pod_stops_answering_its_surfaces(pod_doing_things):
    alice, pod, _schedule, fake, webhook_path, webhook_secret = pod_doing_things
    chat_id = 77701

    await alice.deletes_pod(pod)

    # Deliver exactly as the platform would, to the path Lemma itself
    # registered. Whether the delivery is accepted or refused is Lemma's
    # choice; what must not happen is an agent running and replying for a pod
    # that no longer exists.
    await alice.api.call(
        "POST",
        webhook_path,
        json={
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 1700000000,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "is_bot": False, "first_name": "Sender"},
                "text": "anyone home?",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
    )

    await never(
        lambda: _sent_to(fake, chat_id),
        lambda messages: bool(messages),
        describe="a deleted pod replying on its old surface",
        within=8.0,
    )


async def _sent_to(fake, chat_id):
    return fake.messages_to(chat_id)


@scenario("A deleted pod's standing work stops and stays stopped")
@proves("PS-OPS-020")
@covers("pod.delete", "schedule.list", "pod.deleted")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEV-OPS-003: pod deletion touches only the pod row, so its schedules "
        "stay reachable and report is_active=true — a deleted pod keeps waking "
        "up and running agents nobody can see."
    ),
)
async def test_a_deleted_pod_runs_nothing_further(pod_doing_things):
    alice, pod, schedule, _fake, _path, _secret = pod_doing_things

    # It exists and is armed before the deletion, so the refusal afterwards is
    # about the deletion rather than about the schedule never having been there.
    await alice.opens_schedule(schedule, in_pod=pod)

    await alice.deletes_pod(pod)

    # All three are checked before anything is asserted: which of them still
    # answer is the useful fact, and stopping at the first would report one
    # symptom of a broader hole as though it were the whole of it.
    reachable = {}
    for what, path in (
        ("the schedule itself", f"/pods/{pod['id']}/schedules/{schedule['id']}"),
        ("its run history", f"/pods/{pod['id']}/schedules/{schedule['id']}/runs"),
        ("the pod's schedule list", f"/pods/{pod['id']}/schedules"),
    ):
        answer = await alice.api.call("GET", path)
        if answer.status_code < 400:
            reachable[what] = answer.status_code

    still_says = await alice.api.call(
        "GET", f"/pods/{pod['id']}/schedules/{schedule['id']}"
    )
    state = (
        {
            key: value
            for key, value in still_says.json().items()
            if key in {"status", "enabled", "is_active", "next_run_at", "paused"}
        }
        if still_says.status_code < 400
        else "refused"
    )

    assert not reachable, (
        f"after deleting the pod, its standing work is still reachable: "
        f"{reachable} — and the schedule reports {state}"
    )


@scenario("Deleting a pod leaves every other pod running")
@proves("PS-OPS-020", "PS-POD-051")
@covers("pod.delete", "schedule.list", "pod.get")
async def test_deleting_one_pod_leaves_the_others_working(world):
    alice = await world.new_person("alice")
    await alice.creates_an_organization()
    doomed = await alice.creates_a_pod()
    keeper = await alice.creates_a_pod()
    agent = await alice.creates_an_agent(in_pod=keeper)
    survivor = await alice.creates_a_schedule(
        in_pod=keeper, agent=agent["name"], config={"cron": "0 9 * * *"}
    )

    await alice.deletes_pod(doomed)

    await alice.opens_pod(keeper)
    still_there = {str(s["id"]) for s in await alice.schedules_in(keeper)}
    assert str(survivor["id"]) in still_there, (
        "cleaning up after a deleted pod took another pod's standing work with it"
    )
