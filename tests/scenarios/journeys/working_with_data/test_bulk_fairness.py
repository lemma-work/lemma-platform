"""Working with data → one pod's backlog is not everyone else's problem.

Somebody dropping a folder of documents into a pod is ordinary, and it is also
the moment a shared worker pool can turn into a queue everybody waits behind.

The platform's answer is backpressure: past a point it declines an upload
outright rather than accepting work it cannot stage. That is the right answer,
and it puts three things under test — the refusal has to be legible, it has to
be recoverable, and it must not leak into pods doing something else entirely.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from harness import capability, covers, journey, proves, scenario
from harness.run import a_name_for
from harness.waiting import eventually

pytestmark = [
    journey("Working with data"),
    capability("Put documents in a pod"),
]

#: Deliberately more than the staging pool will take at once, so the scenario
#: is about what happens at the limit rather than about staying under it.
BURST = 12

#: What the platform says when it will not stage another upload right now.
AT_CAPACITY = "UPLOAD_CAPACITY_EXCEEDED"

#: What a conversation in an unrelated pod is allowed to take while another pod
#: is loaded. Generous — this looks for starvation, not for latency.
STILL_PROMPT = 90.0


@pytest.fixture
async def two_pods(world, run):
    """Two pods of this run's own, because this journey fills one on purpose.

    The one place in this suite where a pod per scenario is still right: the
    burst is the point, and a standing pod left holding it would make every
    later document scenario there wait behind work that is never going to
    finish.
    """
    alice = await world.person("daniel")
    busy = await alice.creates_a_pod(named=run.name("busy"))
    quiet = await alice.creates_a_pod(named=run.name("quiet"))
    agent = await alice.creates_an_agent(in_pod=quiet)
    try:
        yield alice, busy, quiet, agent
    finally:
        for pod in (busy, quiet):
            await alice.deletes_pod(pod)


async def _upload(alice, pod, name: str):
    """One upload, returning the response rather than insisting it worked."""
    return await alice.api.call(
        "POST",
        f"/pods/{pod['id']}/datastore/files",
        files={"data": (name, name.encode() + b" " + b"x" * 512, "text/plain")},
        data={"directory_path": "/", "name": name, "search_enabled": "false"},
    )


async def _burst(alice, pod, prefix: str):
    # Named for this run and this burst. The pod is a standing one with other
    # runs' files already in it, and a fixed name would come back 409 CONFLICT —
    # which this scenario would then have to tell apart from the 503 it is
    # actually about. Better to make the collision impossible than to read it.
    stamp = a_name_for(prefix)
    return await asyncio.gather(
        *(_upload(alice, pod, f"{stamp}-{index}.txt") for index in range(BURST))
    )


@scenario("Uploads past what the platform can stage are declined, legibly")
@proves("PS-DATA-042")
@covers("file.upload")
async def test_backpressure_is_legible(two_pods):
    alice, busy, _quiet, _agent = two_pods

    answers = await _burst(alice, busy, "burst")

    accepted = [r for r in answers if r.status_code < 400]
    declined = [r for r in answers if r.status_code >= 400]

    assert accepted, f"nothing was accepted at all: {[r.status_code for r in answers]}"
    # A decline is fine. A decline nobody can act on is not: the caller has to
    # be able to tell "try again shortly" from "this file is wrong", and a bare
    # 500 says neither.
    for refusal in declined:
        assert refusal.status_code == 503, (
            f"an upload was refused with {refusal.status_code} rather than a "
            f"retryable 503: {refusal.text[:200]}"
        )
        assert AT_CAPACITY in refusal.text, (
            f"the refusal does not say it is about capacity, so a client cannot "
            f"know to retry: {refusal.text[:200]}"
        )


@scenario("An upload declined for capacity succeeds when it is tried again")
@proves("PS-DATA-042")
@covers("file.upload", "file.get")
async def test_a_declined_upload_can_be_retried(two_pods):
    alice, busy, _quiet, _agent = two_pods
    await _burst(alice, busy, "pressure")

    # Once the burst has drained, the same upload has to go through. Declining
    # for capacity and declining forever are the same thing to a person who
    # never retries, and only one of them is backpressure.
    # Named once, outside the retry, because the scenario is about *the same*
    # upload going through on a later attempt rather than a different one.
    retried = a_name_for("after-the-rush") + ".txt"
    landed = await eventually(
        lambda: _upload(alice, busy, retried),
        lambda response: response.status_code < 400,
        describe="the staging pool to take an upload again",
        timeout=90.0,
    )

    stored = await alice.opens_file(landed.json()["path"], in_pod=busy)
    assert str(stored.get("path")).endswith(retried), stored


@scenario("A pod taking a burst of uploads does not stall another pod")
@proves("PS-DATA-042")
@covers("file.upload", "agent.conversation.message.send")
async def test_a_burst_does_not_starve_another_pod(two_pods):
    alice, busy, quiet, agent = two_pods

    # Started while the burst is still working through the pool, not after.
    burst = asyncio.create_task(_burst(alice, busy, "loud"))
    started = time.monotonic()
    conversation = await alice.starts_a_conversation(
        in_pod=quiet, with_agent=agent["name"], saying="Still there?"
    )
    messages = await alice.waits_for_a_reply(
        in_conversation=conversation, in_pod=quiet, timeout=STILL_PROMPT
    )
    took = time.monotonic() - started
    await burst

    assert any(message.get("role") == "assistant" for message in messages), messages
    assert took < STILL_PROMPT, (
        f"a conversation in an unrelated pod took {took:.0f}s while another pod "
        f"was uploading {BURST} documents, which is one pod's backlog becoming "
        f"everybody's queue"
    )


@scenario("Every upload the platform accepted is there afterwards")
@proves("PS-DATA-042")
@covers("file.upload", "file.get")
async def test_every_accepted_upload_survives(two_pods):
    alice, busy, _quiet, _agent = two_pods

    answers = await _burst(alice, busy, "kept")
    accepted = [r.json() for r in answers if r.status_code < 400]

    # Accepting an upload and then losing it is the worst outcome available: the
    # person was told it worked, and there is nothing to retry from.
    assert accepted, "the platform accepted none of them"
    for entry in accepted:
        stored = await alice.opens_file(entry["path"], in_pod=busy)
        assert str(stored.get("path")) == str(entry["path"]), stored
