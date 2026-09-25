"""The link a paired computer actually speaks, over a real WebSocket.

Every other Agent Host test drives repositories and services directly. These
open ``/agent-host/link`` through the whole application -- middleware, the
global auth gate, the router -- because that is where the bugs of the HTTP
routes it replaced hid:

* the global `verify_auth` dependency has an allowlist and `/agent-host` was not
  on it, so every host route 401'd -- a paired computer has no user session and
  never will;
* pairing is the one exchange whose credential *is* its body, and nothing
  checked that it works without a session;
* the idle wait called `asyncio.wait_for(anext(...))`, whose timeout cancels and
  closes the async generator, so the second idle round failed and every host
  went OFFLINE five seconds after connecting. The link's push loop waits the
  same way, and an idle link is exercised here across several floor ticks.

These use `async_client` (no session) deliberately. Reaching for
`authenticated_client` here would re-hide exactly what needs proving.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunCheckpoint,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host.channels import poke_host
from app.modules.agent.infrastructure.agent_host.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.infrastructure.agent_host.session_memory import (
    remember_provider_session,
    resume_session_id,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.agent_host.recovery import HOST_REVOKED_DETAIL
from app.modules.agent.infrastructure.agent_host.repository_common import (
    INSTALLATION_REVOKED_MESSAGE,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)
from app.modules.agent.services import agent_host_link_session, agent_host_link_store
from app.modules.agent.tests.e2e.agent_host_helpers import (
    HostLink,
    app_of,
    connected_host,
    conversation_with_a_leased_run,
    hello,
    pair,
    paired_machine,
    publish_harnesses,
    stale_after,
)
from app.modules.test_support.e2e.waiters import eventually

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
        authenticated_client,
        async_client,
        display_name="e2e same machine",
        machine=machine,
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

    stale = await HostLink(app_of(async_client), secret=first["host_secret"]).open()
    try:
        stale.send("hello", {"hello": machine})
        assert await stale.closed() == 4401
    finally:
        await stale.aclose()


@pytest.mark.asyncio
async def test_a_removed_computer_stays_removed_until_the_person_turns_it_back_on(
    authenticated_client, async_client
):
    """Revoking has to stick: the host's auto-connect pairs again within
    seconds, and without the tombstone that silently undid every removal."""
    machine = hello()
    first = await pair(
        authenticated_client, async_client, display_name="e2e removed", machine=machine
    )
    revoked = await authenticated_client.delete(
        f"/me/runtime/agent-hosts/{first['host_id']}"
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text

    minted = await authenticated_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "e2e removed", "organization_id": None},
    )
    body = {
        "pairing_code": minted.json()["pairing_code"],
        "display_name": "e2e removed",
        "hello": machine,
    }
    automatic = await HostLink(app_of(async_client), secret=None).open()
    try:
        refused = await automatic.request("pair", body)
        assert refused["type"] == "error", refused
        assert refused["body"]["code"] == "UNAUTHORIZED"
        assert refused["body"]["message"] == INSTALLATION_REVOKED_MESSAGE
        assert "was removed from this account" in refused["body"]["message"]
        assert await automatic.closed() == 4403
        assert automatic.close_reason == "installation_revoked"
    finally:
        await automatic.aclose()

    # The refused attempt left the code unused, so the person's own
    # "connect again" can still spend it.
    chosen = await HostLink(app_of(async_client), secret=None).open()
    try:
        paired = await chosen.request("pair", {**body, "reenable": True})
        assert paired["type"] == "paired", paired
    finally:
        await chosen.aclose()
    assert paired["body"]["host_id"] == first["host_id"]


@pytest.mark.asyncio
async def test_revoking_a_host_ends_its_runs_and_cancels_its_commands(
    db_session, scenario
):
    """A removed computer can never report back, so nothing may wait on it."""
    await scenario.create_org_with_pod(name_prefix="Revoked")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    cancel = await AgentHostDispatchRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).enqueue_cancel(run_id=run_id)
    assert cancel is not None
    await db_session.commit()

    revoked = await scenario.owner_client.delete(
        f"/me/runtime/agent-hosts/{machine['host_id']}"
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text

    lease = await db_session.get(AgentHostRunLeaseModel, run_id)
    await db_session.refresh(lease)
    assert lease.state == AgentHostRunState.FAILED.value
    assert lease.error_code == "HOST_REVOKED"
    assert lease.error_detail == HOST_REVOKED_DETAIL
    assert lease.terminal_at is not None
    command = await db_session.get(AgentHostCommandModel, cancel.id)
    await db_session.refresh(command)
    assert command.state == AgentHostCommandState.CANCELLED.value


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

    first = await HostLink(app_of(async_client), secret=None).open()
    try:
        paired = await first.request("pair", body)
        assert paired["type"] == "paired", paired
    finally:
        await first.aclose()

    replayed = await HostLink(app_of(async_client), secret=None).open()
    try:
        refused = await replayed.request("pair", body)
        assert refused["type"] == "error"
        assert refused["body"]["code"] == "UNAUTHORIZED"
        assert paired["body"]["host_secret"] not in str(refused)
        assert await replayed.closed() == 4403
    finally:
        await replayed.aclose()


@pytest.mark.asyncio
async def test_the_http_device_routes_only_refuse(async_client):
    """No fallback: a host that still speaks HTTP is told to update, nothing more.

    Through the whole app and without a session, because a protocol-2 host has
    none: a 401 from the global gate would read to it as retryable, and to the
    MCP bridge as "try again", which is the opposite of the point.
    """
    conversation = uuid4()
    for method, path in [
        ("POST", "/agent-host/events/append"),
        ("POST", "/agent-host/events:append"),
        ("POST", "/agent-host/pairings/complete"),
        ("POST", "/agent-host/pairings:complete"),
        ("PUT", "/agent-host/harnesses"),
        ("POST", "/agent-host/revoke"),
        ("POST", f"/agent-runtime/conversations/{conversation}/mcp"),
        ("DELETE", f"/agent-runtime/conversations/{conversation}/mcp"),
        ("GET", f"/agent-runtime/conversations/{conversation}/interactions/call-1"),
    ]:
        response = await async_client.request(
            method,
            path,
            json={} if method != "GET" else None,
            headers={"Authorization": "Bearer some-old-host-secret"},
        )
        assert response.status_code == status.HTTP_410_GONE, (path, response.text)
        assert response.json()["detail"]["code"] == "AGENT_HOST_UPGRADE_REQUIRED"

    # None of them is on the published API surface.
    schema = (await async_client.get("/openapi.json")).json()
    assert not [
        path
        for path in schema["paths"]
        if path.startswith(("/agent-host/", "/agent-runtime/conversations"))
    ]


@pytest.mark.asyncio
async def test_a_protocol_2_poll_marks_the_host_as_needing_an_update(
    authenticated_client, async_client
):
    """The one old route whose answer the old host acts on, and what the
    person then sees; the new host reconnecting over the link clears it."""
    paired = await pair(authenticated_client, async_client, display_name="e2e old")

    for _ in range(2):  # the second changes nothing, and logs nothing
        polled = await async_client.post(
            "/agent-host/poll",
            json={"hello": {**paired["hello"], "protocol_version": 2}},
            headers={"Authorization": f"Bearer {paired['host_secret']}"},
        )
        assert polled.status_code == status.HTTP_200_OK, polled.text
        assert polled.json() == {
            "protocol_version": 3,
            "host_status": "UPGRADE_REQUIRED",
            "commands": [],
            "poll_after_ms": 30_000,
        }

    hosts = (await authenticated_client.get("/me/runtime/agent-hosts")).json()
    [host] = [item for item in hosts["items"] if item["id"] == paired["host_id"]]
    assert host["status"] == "UPGRADE_REQUIRED"

    # An unknown secret learns nothing it could not already guess.
    stranger = await async_client.post(
        "/agent-host/poll", json={}, headers={"Authorization": "Bearer nobody"}
    )
    assert stranger.json() == polled.json()

    link = await connected_host(app_of(async_client), paired)
    try:
        hosts = (await authenticated_client.get("/me/runtime/agent-hosts")).json()
        [host] = [item for item in hosts["items"] if item["id"] == paired["host_id"]]
        assert host["status"] == "ONLINE"
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_an_idle_link_stays_up_across_several_floor_ticks(
    authenticated_client, async_client, monkeypatch
):
    """An idle link must survive the rounds that used to kill the poll.

    The push floor is shortened so this crosses several idle rounds in well
    under a second; at the shipped 5 seconds it would take half a minute.
    """
    monkeypatch.setattr(agent_host_link_session, "PUSH_FLOOR_SECONDS", 0.1)
    paired = await pair(authenticated_client, async_client, display_name="e2e idle")
    link = await connected_host(app_of(async_client), paired)
    try:
        await asyncio.sleep(0.6)
        answer = await link.request("control", {})
        assert answer["type"] == "control_ok", answer
        assert answer["body"] == {"commands": [], "refused": []}
        assert link.close_code is None
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_the_link_refuses_an_unknown_missing_or_revoked_secret(
    authenticated_client, async_client
):
    paired = await pair(authenticated_client, async_client, display_name="e2e revoked")
    app = app_of(async_client)

    for secret, code in [("not-a-real-host-secret", 4401), (None, 4403)]:
        link = await HostLink(app, secret=secret).open()
        try:
            link.send("hello", {"hello": paired["hello"]})
            assert await link.closed() == code
        finally:
            await link.aclose()

    revoked = await authenticated_client.delete(
        f"/me/runtime/agent-hosts/{paired['host_id']}"
    )
    assert revoked.status_code == status.HTTP_200_OK, revoked.text

    after = await HostLink(app, secret=paired["host_secret"]).open()
    try:
        after.send("hello", {"hello": paired["hello"]})
        assert await after.closed() == 4401
        assert after.close_reason == "AGENT_HOST_REVOKED_OR_MISSING"
    finally:
        await after.aclose()


@pytest.mark.asyncio
async def test_revoking_a_host_closes_the_link_it_already_has(
    authenticated_client, async_client
):
    """The secret dies at commit; the socket that authenticated before then
    has to be told, or it keeps working until the host next reconnects."""
    paired = await pair(authenticated_client, async_client, display_name="e2e live")
    link = await connected_host(app_of(async_client), paired)
    try:
        revoked = await authenticated_client.delete(
            f"/me/runtime/agent-hosts/{paired['host_id']}"
        )
        assert revoked.status_code == status.HTTP_200_OK, revoked.text
        assert await link.closed() == 4401
    finally:
        await link.aclose()


@pytest.mark.asyncio
async def test_a_newer_link_for_the_same_host_supersedes_the_older(
    authenticated_client, async_client
):
    """A network drop can leave a half-open socket on some replica; the host's
    reconnect must win, so commands go out on one socket at a time."""
    paired = await pair(authenticated_client, async_client, display_name="e2e twice")
    app = app_of(async_client)
    older = await connected_host(app, paired)
    newer = None
    try:
        await asyncio.sleep(0.2)  # let the older link's subscription settle
        newer = await connected_host(app, paired)
        assert await older.closed() == 4409
        answer = await newer.request("control", {})
        assert answer["type"] == "control_ok", answer
    finally:
        await older.aclose()
        if newer is not None:
            await newer.aclose()


@pytest.mark.asyncio
async def test_a_paired_host_publishes_the_harnesses_the_workspace_can_use(
    authenticated_client, async_client
):
    """Publishing is what turns a paired machine into pickable chat models."""
    paired = await pair(authenticated_client, async_client, display_name="e2e agents")

    published = await publish_harnesses(
        async_client,
        paired,
        [
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
        ],
    )
    assert published["type"] == "harnesses_ok", published

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
        ),
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
        ),
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
        ),
    )
    await remember_provider_session(
        uow,
        AgentHostRunCheckpoint(
            run_id=run_id,
            lease_epoch=1,
            state=AgentHostRunState.SUCCEEDED,
            detail={"stop_reason": "end_turn"},
        ),
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


@pytest.mark.asyncio
async def test_a_checkpoint_on_the_link_renews_the_lease(db_session, scenario):
    """``control`` is the heartbeat, and its checkpoint is the lease renewal.

    A run's lease lapses in 90 seconds; a host whose checkpoints did not land
    would see its runs reconciled away from under it while still working.
    """
    await scenario.create_org_with_pod(name_prefix="Heartbeat")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    await db_session.commit()
    link = await connected_host(
        app_of(scenario.async_client), machine, capacity=_capacity(1)
    )
    try:
        answer = await link.request(
            "control",
            {
                "capacity": _capacity(1),
                "checkpoints": [
                    {
                        "run_id": str(run_id),
                        "lease_epoch": 1,
                        "state": AgentHostRunState.RUNNING.value,
                        "detail": {"provider_session_id": "rollout-7"},
                    },
                    {"run_id": str(run_id), "lease_epoch": 0, "state": "RUNNING"},
                ],
            },
        )
    finally:
        await link.aclose()

    assert answer["type"] == "control_ok", answer
    assert [(r["kind"], r["index"]) for r in answer["body"]["refused"]] == [
        ("checkpoint", 1)
    ]
    lease = await AgentHostDispatchRepository(
        SqlAlchemyUnitOfWork(db_session)
    ).get_run_lease(run_id=run_id)
    await db_session.refresh(lease)
    assert lease.state == AgentHostRunState.RUNNING.value


@pytest.mark.asyncio
async def test_a_queued_command_is_pushed_when_the_host_is_poked(
    db_session, scenario, monkeypatch
):
    """A cancel must not wait for the next heartbeat, or even the floor."""
    monkeypatch.setattr(agent_host_link_session, "PUSH_FLOOR_SECONDS", 60.0)
    await scenario.create_org_with_pod(name_prefix="Pushed")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    await db_session.commit()
    link = await connected_host(
        app_of(scenario.async_client), machine, capacity=_capacity(1)
    )
    try:
        await asyncio.sleep(0.2)  # the push loop subscribes after welcome
        cancel = await AgentHostDispatchRepository(
            SqlAlchemyUnitOfWork(db_session)
        ).enqueue_cancel(run_id=run_id)
        await db_session.commit()
        await poke_host(machine["host_id"])

        pushed = await link.next_push(timeout=10)
    finally:
        await link.aclose()

    assert pushed["type"] == "commands", pushed
    assert str(cancel.id) in {c["command_id"] for c in pushed["body"]["commands"]}


@pytest.mark.asyncio
async def test_a_cancel_is_delivered_ahead_of_starts_the_host_cannot_run(
    db_session, scenario, monkeypatch
):
    """Commands are fetched under a limit, and a START_RUN the host has no slot
    for is skipped *after* it has consumed a row of that limit. Enough queued
    starts therefore buried every CANCEL_RUN behind them — at exactly the moment
    cancelling matters most, because the host is saturated."""
    monkeypatch.setattr(agent_host_link_store, "MAX_COMMANDS_PER_READ", 3)
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

    link = await connected_host(
        app_of(scenario.async_client), machine, capacity=_capacity(0)
    )
    try:
        answer = await link.request("control", {"capacity": _capacity(0)})
        # A command goes out once, by whichever path reaches it first, and
        # both run at once: the pusher wakes on the link's own announcement
        # after ``hello``. Both read the queue under SKIP LOCKED, so while the
        # pusher's transaction holds the cancel, ``control`` skips it and is
        # answered empty -- and the push lands after that answer. So watch
        # every delivery until the cancel arrives, rather than looking once.
        delivered = list(answer["body"]["commands"])

        async def delivered_so_far() -> list[dict]:
            delivered.extend(
                command
                for frame in link.pushed_so_far()
                if frame["type"] == "commands"
                for command in frame["body"]["commands"]
            )
            return delivered

        # Within the 5-second push floor even if every poke were lost; a
        # cancel buried behind the starts would never arrive at all.
        await eventually(
            label="the cancel reaching a saturated host",
            probe=delivered_so_far,
            done=lambda commands: any(
                command["kind"] == AgentHostCommandKind.CANCEL_RUN.value
                for command in commands
            ),
            timeout_seconds=15,
        )
    finally:
        await link.aclose()

    # The host has no slot, so the cancel is all it may be handed -- once,
    # whichever path carried it -- and none of the starts in front of it.
    assert [command["kind"] for command in delivered] == [
        AgentHostCommandKind.CANCEL_RUN.value
    ]


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
async def test_two_machines_linked_at_once_both_answer(
    authenticated_client, async_client
):
    """Two machines idling at once is the normal state of a workspace."""
    first = await pair(authenticated_client, async_client, display_name="e2e a")
    second = await pair(authenticated_client, async_client, display_name="e2e b")
    app = app_of(async_client)
    links = [await connected_host(app, host) for host in (first, second)]
    try:
        answers = await asyncio.gather(*(link.request("control", {}) for link in links))
    finally:
        for link in links:
            await link.aclose()

    assert [answer["type"] for answer in answers] == ["control_ok", "control_ok"]


@pytest.mark.asyncio
async def test_events_are_acknowledged_and_a_gap_is_named(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Events")
    machine = await paired_machine(scenario)
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
    )
    await db_session.commit()

    def batch(*sequences: int) -> dict:
        return {
            "events": [
                {
                    "run_id": str(run_id),
                    "lease_epoch": 1,
                    "sequence": sequence,
                    "type": "agent_message_chunk",
                    "payload": {"text": "hi"},
                }
                for sequence in sequences
            ]
        }

    link = await connected_host(app_of(scenario.async_client), machine)
    try:
        acked = await link.request("events", batch(1, 2))
        gap = await link.request("events", batch(5))
        stale = await link.request(
            "events", {"events": [{**batch(3)["events"][0], "lease_epoch": 2}]}
        )
    finally:
        await link.aclose()
        await AgentHostDispatchRepository(
            SqlAlchemyUnitOfWork(db_session)
        ).delete_run_events(run_id=run_id)

    assert acked["body"]["ack"]["acked_through"] == 2
    assert gap["body"]["code"] == "SEQUENCE_GAP"
    assert stale["body"]["code"] == "STALE_LEASE"
