"""Platform → work dispatched to a paired Agent Host.

PS-AGENT-041 says dispatched work runs *exactly once*. Until now the only proof
was a refusal: an unpaired host is told no. This scenario walks the pairing in
the product's own order — mint a code, spend it, announce harnesses, bind a
runtime profile, pin an agent, start a run — and then asks the question the
promise is actually about: does the host claim the run once, however many times
it polls?
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from harness import capability, covers, journey, proves, scenario
from harness.waiting import eventually, UNTIL_A_RUN_SETTLES

pytestmark = [journey("Platform"), capability("Agent hosts")]

_HELLO = {
    "installation_id": "scenarios-install",
    "host_release": "scenarios-1.0.0",
    "protocol_version": 2,
}

#: The poll long-polls server-side for up to half a minute before answering an
#: idle host, so each request needs a client timeout past that window.
_POLL_TIMEOUT = 45.0


def _host_auth(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def _harness_snapshot() -> dict:
    return {
        "harness_key": f"scenarios-codex-{uuid4().hex[:8]}",
        "display_name": "Scenario Codex",
        "adapter_version": "1.0.0",
        "health": "READY",
        "config_revision": "rev-1",
        "config_options": [],
        "stale_after": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
    }


async def _poll(alice, secret: str, **extra):
    return await alice.api.call(
        "POST",
        "/agent-host/poll",
        headers=_host_auth(secret),
        timeout=_POLL_TIMEOUT,
        json={
            "hello": _HELLO,
            "capacity": {"max_runs": 2, "active_runs": 0, "available_runs": 2},
            **extra,
        },
    )


async def _pair_and_publish(alice) -> str:
    """The user mints a code; the machine spends it for a secret shown once."""
    pairing = await alice.api.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "Scenario laptop"},
    )
    completed = await alice.api.call(
        "POST",
        "/agent-host/pairings:complete",
        json={
            "pairing_code": pairing["pairing_code"],
            "display_name": "Scenario laptop",
            "hello": _HELLO,
        },
    )
    assert completed.status_code == 200, completed.text[:300]
    host_secret = str(completed.json()["host_secret"])

    # The machine announces its harness.
    published = await alice.api.call(
        "PUT",
        "/agent-host/harnesses",
        headers=_host_auth(host_secret),
        json={"harnesses": [_harness_snapshot()]},
    )
    assert published.status_code == 200, published.text[:300]

    # A first poll registers the heartbeat: profile creation checks that this
    # harness's host has been seen, as it would for a real desktop.
    seen = await _poll(alice, host_secret)
    assert seen.status_code == 200, seen.text[:300]
    return host_secret


@scenario("A paired host is offered dispatched work once, under one claim")
@proves("PS-AGENT-041")
@covers(
    "agent.host.pairing.create",
    "agent.host.pairing.complete",
    "agent.host.harnesses.publish",
    "agent.runtime.profiles.create",
    "agent.update",
    "agent.conversation.message.send",
    "agent.host.poll",
)
async def test_dispatched_work_is_claimed_exactly_once(world):
    alice = await world.person("daniel")
    organization = alice.organization
    pod = await alice.works_in("company-wide")

    host_secret = await _pair_and_publish(alice)

    # The profile binds to the harness id, so read back what the host published.
    hosts = await alice.api.get("/me/runtime/agent-hosts")
    listed = hosts if isinstance(hosts, list) else hosts.get("items", [])
    [host] = listed
    harness_list = await alice.api.get(f"/me/runtime/agent-hosts/{host['id']}/harnesses")
    rows = harness_list if isinstance(harness_list, list) else harness_list["items"]
    assert rows, "the paired host announces no harnesses"
    harness_id = rows[0]["id"]

    profile = await alice.api.post(
        f"/organizations/{organization['id']}/agent-runtime/profiles",
        json={
            "source": "AGENT_HOST",
            "harness_id": harness_id,
            "name": f"scenario-host-{uuid4().hex[:6]}",
        },
    )

    agent = await alice.creates_an_agent(
        in_pod=pod,
        instruction="Work is done on the paired machine.",
    )
    pinned = await alice.api.call(
        "PATCH",
        f"/pods/{pod['id']}/agents/{agent['name']}",
        json={"agent_runtime": {"profile_id": str(profile["id"])}},
    )
    assert pinned.status_code < 400, pinned.text[:300]

    # What this host is already holding. The scenario deliberately never
    # finishes the run it dispatches — nothing here does the work — so the claim
    # it makes stands for good, and a tenant that has seen an earlier run has an
    # earlier claim on it. Subtracting them keeps the question the same one it
    # always was: did *this* message become exactly one claim.
    standing = await _poll(alice, host_secret)
    outstanding = {
        command["command_id"]
        for command in (
            standing.json().get("commands", []) if standing.status_code == 200 else []
        )
        if command.get("kind") == "START_RUN"
    }

    conversation = await alice.starts_a_conversation(in_pod=pod, with_agent=agent["name"])
    # The send endpoint streams until the run finishes, and this run finishes
    # only when a host does the work — which is the thing under test. Send
    # without holding the stream: once the message commits, the run is
    # dispatched, and that is everything this scenario needs from it.
    sending = asyncio.create_task(
        alice.says("Do the thing.", in_conversation=conversation, in_pod=pod)
    )

    try:
        # The claim: whatever else a poll carries, the run reaches this host as
        # ONE START_RUN command — and after acknowledging it, never again.

        async def first_claim():
            answer = await _poll(alice, host_secret)
            if answer.status_code != 200:
                return None
            commands = [
                c
                for c in answer.json().get("commands", [])
                if c.get("kind") == "START_RUN" and c.get("command_id") not in outstanding
            ]
            return commands or None

        commands = await eventually(
            first_claim,
            bool,
            describe="the paired host to be offered the dispatched run",
            timeout=UNTIL_A_RUN_SETTLES,
        )
        assert len(commands) == 1, (
            f"one message became {len(commands)} START_RUN claims on the host: "
            f"{[c['command_id'] for c in commands]}"
        )

        # Transport handout is deliberately at-least-once: the same claim
        # comes back on later polls until the run is finished, because a host
        # that missed a reply has to be able to pick up where it left off.
        # Exactly-once lives at execution, in the lease epoch: what must never
        # happen is the run being handed out under two different claims — a
        # second command id, or a bumped epoch while this claim still stands,
        # means somebody else was told to run the same work too.
        [claim] = commands
        for _ in range(2):
            answer = await _poll(alice, host_secret)
            assert answer.status_code == 200, answer.text[:300]
            offers = [
                c
                for c in answer.json().get("commands", [])
                if c.get("kind") == "START_RUN" and c.get("command_id") not in outstanding
            ]
            assert len(offers) <= 1, (
                f"a single handout carried {len(offers)} START_RUN commands "
                f"for one run: {[c['command_id'] for c in offers]}"
            )
            for offer in offers:
                assert offer["command_id"] == claim["command_id"] and (
                    offer["lease_epoch"] == claim["lease_epoch"]
                ), (
                    f"the run was handed out under two different claims: "
                    f"first ({claim['command_id']}, epoch "
                    f"{claim['lease_epoch']}), then ({offer['command_id']}, "
                    f"epoch {offer['lease_epoch']})"
                )
    finally:
        # The send was never meant to complete: it streams until the run
        # finishes, and nothing here ever does the work. Cancel it and reap it
        # so the task does not outlive the scenario. Its outcome is deliberately
        # discarded -- the claim assertions above are what this proves, and a
        # failure raised out of cleanup would replace whichever one of them
        # actually failed.
        sending.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await sending
