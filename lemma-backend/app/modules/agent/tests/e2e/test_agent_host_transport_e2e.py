"""The HTTP surface a paired computer actually speaks, over real HTTP.

Every other Agent Host test drives repositories and services directly, so no
test had ever issued a request to `/agent-host/*`. Three bugs shipped behind
that gap, each hiding the next:

* the global `verify_auth` dependency has an allowlist and `/agent-host` was not
  on it, so every one of these routes 401'd - a paired computer has no user
  session and never will;
* `pairings:complete` is the one route whose credential *is* its body, and
  nothing checked that it works without a session;
* the idle poll called `asyncio.wait_for(anext(...))`, whose timeout cancels and
  closes the async generator, so the second idle round raised
  StopAsyncIteration and 500'd - every host went OFFLINE five seconds after
  connecting.

These use `async_client` (no session) deliberately. Reaching for
`authenticated_client` here would re-hide exactly what needs proving.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.api.controllers import agent_host_controller
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host_session_memory import (
    remember_provider_session,
    resume_session_id,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostCommandModel
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    hello,
    pair,
    paired_machine,
    stale_after,
)

pytestmark = pytest.mark.e2e

@pytest.mark.asyncio
async def test_a_machine_pairs_without_a_user_session(
    authenticated_client, async_client
):
    """The pairing code is the credential. Requiring a session here 401s the
    only caller this route has."""
    paired = await pair(authenticated_client, async_client, display_name="e2e laptop")

    assert paired["host_secret"]
    assert paired["host_id"]

    listed = await authenticated_client.get("/me/runtime/agent-hosts")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert paired["host_id"] in {item["id"] for item in listed.json()["items"]}
    # The secret is issued exactly once and never read back.
    assert paired["host_secret"] not in listed.text


@pytest.mark.asyncio
async def test_re_pairing_the_same_machine_updates_it_instead_of_duplicating(
    authenticated_client, async_client
):
    """One physical machine is one paired computer, however often you pair it.

    Identity is (user_id, installation_id) — the machine's own id, with no
    organization in it. Pairing again rotates the secret on the row that is
    already there, which is what lets re-pairing be safe to repeat
    and what stops one laptop appearing twice in a workspace's list.
    """
    machine = hello()

    first = await pair(
        authenticated_client, async_client, display_name="e2e same machine", machine=machine
    )
    second = await pair(
        authenticated_client, async_client, display_name="e2e renamed", machine=machine
    )

    assert second["host_id"] == first["host_id"]
    # The secret really is re-issued, so the old one stops working.
    assert second["host_secret"] != first["host_secret"]

    listed = await authenticated_client.get("/me/runtime/agent-hosts")
    matching = [
        item for item in listed.json()["items"] if item["id"] == first["host_id"]
    ]
    assert len(matching) == 1
    assert matching[0]["display_name"] == "e2e renamed"

    poll_body = {
        "hello": machine,
        "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
    }
    stale = await async_client.post(
        "/agent-host/poll",
        json=poll_body,
        headers={"Authorization": f"Bearer {first['host_secret']}"},
    )
    assert stale.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_a_pairing_code_is_single_use(authenticated_client, async_client):
    minted = await authenticated_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "e2e once", "organization_id": None},
    )
    body = {
        "pairing_code": minted.json()["pairing_code"],
        "display_name": "e2e once",
        "hello": hello(),
    }

    first = await async_client.post("/agent-host/pairings:complete", json=body)
    assert first.status_code == status.HTTP_200_OK, first.text

    replayed = await async_client.post("/agent-host/pairings:complete", json=body)
    assert replayed.status_code != status.HTTP_200_OK
    assert first.json()["host_secret"] not in replayed.text


@pytest.mark.asyncio
async def test_the_host_polls_with_its_secret_and_survives_an_idle_round(
    authenticated_client, async_client, monkeypatch
):
    """A poll that finds no work must stay a 200 across several idle rounds.

    The idle wait is shortened so this exercises more than one round in about a
    second; at the shipped 5s/25s it would take half a minute to reach the
    round that used to fail.
    """
    monkeypatch.setattr(agent_host_controller, "_IDLE_REPOLL_SECONDS", 0.2)
    monkeypatch.setattr(agent_host_controller, "_LONG_POLL_SECONDS", 1.0)

    paired = await pair(authenticated_client, async_client, display_name="e2e poller")
    headers = {"Authorization": f"Bearer {paired['host_secret']}"}
    poll_body = {
        "hello": paired["hello"],
        "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
    }

    polled = await async_client.post(
        "/agent-host/poll", json=poll_body, headers=headers
    )
    assert polled.status_code == status.HTTP_200_OK, polled.text
    assert polled.json()["commands"] == []

    # Polling again is what the host does forever; it must not degrade.
    again = await async_client.post("/agent-host/poll", json=poll_body, headers=headers)
    assert again.status_code == status.HTTP_200_OK, again.text


@pytest.mark.asyncio
async def test_polling_refuses_an_unknown_or_revoked_secret(
    authenticated_client, async_client
):
    paired = await pair(authenticated_client, async_client, display_name="e2e revoked")
    poll_body = {
        "hello": paired["hello"],
        "capacity": {"max_runs": 1, "active_runs": 0, "available_runs": 1},
    }

    unknown = await async_client.post(
        "/agent-host/poll",
        json=poll_body,
        headers={"Authorization": "Bearer not-a-real-host-secret"},
    )
    assert unknown.status_code == status.HTTP_401_UNAUTHORIZED

    missing = await async_client.post("/agent-host/poll", json=poll_body)
    assert missing.status_code == status.HTTP_401_UNAUTHORIZED

    revoked = await authenticated_client.delete(
        f"/me/runtime/agent-hosts/{paired['host_id']}"
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text

    after = await async_client.post(
        "/agent-host/poll",
        json=poll_body,
        headers={"Authorization": f"Bearer {paired['host_secret']}"},
    )
    assert after.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_a_paired_host_publishes_the_harnesses_the_workspace_can_use(
    authenticated_client, async_client
):
    """Publishing is what turns a paired machine into pickable chat models."""
    paired = await pair(authenticated_client, async_client, display_name="e2e agents")
    headers = {"Authorization": f"Bearer {paired['host_secret']}"}

    published = await async_client.put(
        "/agent-host/harnesses",
        json={
            "harnesses": [
                {
                    "harness_key": "opencode",
                    "display_name": "OpenCode",
                    "adapter_version": "1.0.0",
                    "upstream_version": "0.1.0",
                    "health": "READY",
                    "config_revision": "rev-1",
                    "config_options": [],
                    "stale_after": stale_after(),
                }
            ]
        },
        headers=headers,
    )
    assert published.status_code == status.HTTP_200_OK, published.text

    harnesses = await authenticated_client.get(
        f"/me/runtime/agent-hosts/{paired['host_id']}/harnesses"
    )
    assert harnesses.status_code == status.HTTP_200_OK, harnesses.text
    assert "opencode" in {item["harness_key"] for item in harnesses.json()["items"]}


@pytest.mark.asyncio
async def test_the_session_a_host_reports_comes_back_on_the_next_turn(
    db_session, scenario
):
    """One conversation is one provider session, across turns.

    Without this the agent meets the user again on every message: it cannot see
    what it just said, so it re-asks answered questions and contradicts itself.
    """
    await scenario.create_org_with_pod(name_prefix="Session")
    machine = await paired_machine(scenario)
    host_id, harness_id = machine["host_id"], machine["harness_id"]
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session, scenario, host_id=host_id, harness_id=harness_id
    )
    uow = SqlAlchemyUnitOfWork(db_session)

    await remember_provider_session(
        uow,
        AgentHostRunCheckpoint(
            run_id=run_id,
            lease_epoch=1,
            state=AgentHostRunState.DISPATCHING,
            detail={"provider_session_id": "rollout-42"},
        )
    )

    assert (
        await resume_session_id(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            capabilities={"load_session": True},
        )
        == "rollout-42"
    )


@pytest.mark.asyncio
async def test_a_session_is_not_offered_to_a_harness_that_cannot_use_it(
    db_session, scenario
):
    """A Codex rollout id means nothing to Claude Code.

    Handing it over would fail a `session/load` on every turn before falling
    back, so neither a different harness nor one that never advertised
    `loadSession` is offered the stored id.
    """
    await scenario.create_org_with_pod(name_prefix="Session")
    machine = await paired_machine(scenario)
    host_id, harness_id = machine["host_id"], machine["harness_id"]
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session, scenario, host_id=host_id, harness_id=harness_id
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    await remember_provider_session(
        uow,
        AgentHostRunCheckpoint(
            run_id=run_id,
            lease_epoch=1,
            state=AgentHostRunState.DISPATCHING,
            detail={"provider_session_id": "rollout-42"},
        )
    )

    assert (
        await resume_session_id(
            uow,
            conversation_id=conversation_id,
            harness_id=uuid4(),
            capabilities={"load_session": True},
        )
        is None
    )
    assert (
        await resume_session_id(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            capabilities={"load_session": False},
        )
        is None
    )


@pytest.mark.asyncio
async def test_a_checkpoint_without_a_session_leaves_the_stored_one_alone(
    db_session, scenario
):
    """Only the dispatching checkpoint carries the id; the rest must not erase
    it, or a conversation would lose its memory the moment a run finished."""
    await scenario.create_org_with_pod(name_prefix="Session")
    machine = await paired_machine(scenario)
    host_id, harness_id = machine["host_id"], machine["harness_id"]
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session, scenario, host_id=host_id, harness_id=harness_id
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    await remember_provider_session(
        uow,
        AgentHostRunCheckpoint(
            run_id=run_id,
            lease_epoch=1,
            state=AgentHostRunState.DISPATCHING,
            detail={"provider_session_id": "rollout-42"},
        )
    )
    await remember_provider_session(
        uow,
        AgentHostRunCheckpoint(
            run_id=run_id,
            lease_epoch=1,
            state=AgentHostRunState.SUCCEEDED,
            detail={"stop_reason": "end_turn"},
        )
    )

    assert (
        await resume_session_id(
            uow,
            conversation_id=conversation_id,
            harness_id=harness_id,
            capabilities={"load_session": True},
        )
        == "rollout-42"
    )


def _capacity(available: int, *, max_runs: int = 2) -> dict:
    return {
        "max_runs": max_runs,
        "active_runs": max_runs - available,
        "available_runs": available,
    }


async def _elapsed_poll(async_client, machine, *, capacity: dict, **body) -> tuple:
    """One poll, and how long the server held it."""
    started = asyncio.get_running_loop().time()
    response = await async_client.post(
        "/agent-host/poll",
        json={"hello": machine["hello"], "capacity": capacity, **body},
        headers={"Authorization": f"Bearer {machine['host_secret']}"},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json(), asyncio.get_running_loop().time() - started


@pytest.mark.asyncio
async def test_a_saturated_host_waits_instead_of_being_told_to_come_straight_back(
    db_session, scenario, monkeypatch
):
    """A host with no free slot used to be answered instantly with a zero
    backoff, so it re-polled at round-trip speed for as long as it stayed busy —
    and a machine left draining did it indefinitely. It has to wait like any
    other idle host; a poke is what wakes it when a cancel arrives."""
    monkeypatch.setattr(agent_host_controller, "_IDLE_REPOLL_SECONDS", 0.2)
    monkeypatch.setattr(agent_host_controller, "_LONG_POLL_SECONDS", 0.8)
    await scenario.create_org_with_pod(name_prefix="Saturated")
    machine = await paired_machine(scenario)

    body, elapsed = await _elapsed_poll(
        scenario.async_client, machine, capacity=_capacity(0)
    )

    assert body["commands"] == []
    assert body["poll_after_ms"] == 0
    assert elapsed >= 0.7, "a saturated host was answered instantly and would spin"


@pytest.mark.asyncio
async def test_a_repeated_heartbeat_is_not_mistaken_for_news(
    db_session, scenario, monkeypatch
):
    """A non-terminal checkpoint *is* the lease heartbeat, so the host resends
    it every poll for the life of the run. Treating that as a control update
    meant a busy host never long-polled once: it round-tripped at 1Hz for the
    whole run, rewriting a lease row and a conversation-metadata row each time.

    The first checkpoint really does advance the run and is answered promptly;
    the identical second one must be allowed to wait.
    """
    monkeypatch.setattr(agent_host_controller, "_IDLE_REPOLL_SECONDS", 0.2)
    monkeypatch.setattr(agent_host_controller, "_LONG_POLL_SECONDS", 0.8)
    await scenario.create_org_with_pod(name_prefix="Heartbeat")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    await db_session.commit()
    heartbeat = {
        "checkpoints": [
            {
                "run_id": str(run_id),
                "lease_epoch": 1,
                "state": AgentHostRunState.RUNNING.value,
                "detail": {"provider_session_id": "rollout-7"},
            }
        ]
    }

    advanced, advanced_elapsed = await _elapsed_poll(
        scenario.async_client, machine, capacity=_capacity(1), **heartbeat
    )
    repeated, repeated_elapsed = await _elapsed_poll(
        scenario.async_client, machine, capacity=_capacity(1), **heartbeat
    )

    assert advanced["poll_after_ms"] > 0
    assert advanced_elapsed < 0.7, "a real state advance should answer promptly"
    assert repeated["poll_after_ms"] == 0
    assert repeated_elapsed >= 0.7, "a repeated heartbeat kept cutting the poll short"


@pytest.mark.asyncio
async def test_a_cancel_is_delivered_ahead_of_starts_the_host_cannot_run(
    db_session, scenario, monkeypatch
):
    """Commands are fetched under a limit, and a START_RUN the host has no slot
    for is skipped *after* it has consumed a row of that limit. Enough queued
    starts therefore buried every CANCEL_RUN behind them — at exactly the moment
    cancelling matters most, because the host is saturated."""
    monkeypatch.setattr(agent_host_controller, "_MAX_COMMANDS_PER_POLL", 3)
    await scenario.create_org_with_pod(name_prefix="Starved")
    machine = await paired_machine(scenario)
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )

    now = datetime.now(timezone.utc)
    for index in range(5):
        # COMPLETED so these can share a conversation: only one run may be
        # active per conversation, and what matters here is the command queue,
        # not the runs behind it.
        queued = AgentRunModel(
            conversation_id=conversation_id,
            status="COMPLETED",
            started_at=now,
        )
        db_session.add(queued)
        await db_session.flush()
        db_session.add(
            AgentHostCommandModel(
                host_id=machine["host_id"],
                run_id=queued.id,
                kind=AgentHostCommandKind.START_RUN.value,
                lease_epoch=1,
                payload={},
                state=AgentHostCommandState.QUEUED.value,
                created_at=now - timedelta(minutes=10 - index),
                expires_at=now + timedelta(minutes=5),
            )
        )
    # Queued last, so ordering by age alone would never reach it.
    db_session.add(
        AgentHostCommandModel(
            host_id=machine["host_id"],
            run_id=run_id,
            kind=AgentHostCommandKind.CANCEL_RUN.value,
            lease_epoch=1,
            payload={"agent_run_id": str(run_id)},
            state=AgentHostCommandState.QUEUED.value,
            created_at=now,
            expires_at=now + timedelta(minutes=5),
        )
    )
    await db_session.commit()

    body, _ = await _elapsed_poll(
        scenario.async_client, machine, capacity=_capacity(0)
    )

    assert AgentHostCommandKind.CANCEL_RUN.value in {
        command["kind"] for command in body["commands"]
    }


@pytest.mark.asyncio
async def test_a_second_cancel_for_the_same_lease_is_not_queued(db_session, scenario):
    """Every path that abandons a run asks the host to stop it — the deadline,
    a stop request, a stream outage. They all say the same thing, and each extra
    command occupies a slot in the poll's limit."""
    await scenario.create_org_with_pod(name_prefix="Cancel")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    repository = AgentHostDispatchRepository(SqlAlchemyUnitOfWork(db_session))

    first = await repository.enqueue_cancel(run_id=run_id)
    second = await repository.enqueue_cancel(run_id=run_id)

    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_concurrent_idle_polls_all_return(
    authenticated_client, async_client, monkeypatch
):
    """Two machines idling at once is the normal state of a workspace."""
    monkeypatch.setattr(agent_host_controller, "_IDLE_REPOLL_SECONDS", 0.2)
    monkeypatch.setattr(agent_host_controller, "_LONG_POLL_SECONDS", 1.0)

    first = await pair(authenticated_client, async_client, display_name="e2e a")
    second = await pair(authenticated_client, async_client, display_name="e2e b")
    responses = await asyncio.gather(
        *(
            async_client.post(
                "/agent-host/poll",
                json={
                    "hello": host["hello"],
                    "capacity": {
                        "max_runs": 1,
                        "active_runs": 0,
                        "available_runs": 1,
                    },
                },
                headers={"Authorization": f"Bearer {host['host_secret']}"},
            )
            for host in (first, second)
        )
    )

    assert [response.status_code for response in responses] == [
        status.HTTP_200_OK,
        status.HTTP_200_OK,
    ]
