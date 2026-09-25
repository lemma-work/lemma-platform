"""The Agent Host link, frame by frame, against stand-ins for its collaborators.

Each test drives a session the way a host would -- frames in, frames and a
close code out -- and asserts what the host would see. The close codes and
error codes are the host's whole vocabulary for what went wrong, so getting one
wrong is not cosmetic: 4401 three times in a row makes the host drop its
pairing, and 4426 stops it for good until Desktop updates it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid7

import pytest

from app.modules.agent.domain.agent_host import AgentHostCommandKind
from app.modules.agent.domain.agent_host_link import (
    REVOKED_OR_MISSING_REASON,
    LinkCloseCode,
)
from app.modules.agent.infrastructure.agent_host.channels import (
    host_poke_channel,
)
from app.modules.agent.infrastructure.agent_host.repository_common import (
    AgentHostNotFound,
    AgentHostProtocolViolation,
    AgentHostSequenceGap,
    AgentHostStaleLease,
    AgentHostTerminalRun,
)
from app.modules.agent.services.agent_host_link_registry import (
    AgentHostLinkRegistry,
)
from app.modules.agent.services.realtime import conversation_channel
from app.modules.agent.tests.unit.agent_host_link_fakes import (
    PAIRING_CODE,
    SECRET,
    TOKEN,
    FakeChannels,
    FakeSocket,
    FakeStore,
    Link,
    command,
    hello,
)


def _events_body(run_id=None, sequences=(1, 2)) -> dict:
    run_id = run_id or uuid7()
    return {
        "events": [
            {
                "run_id": str(run_id),
                "lease_epoch": 1,
                "sequence": sequence,
                "type": "agent_message_chunk",
                "payload": {"text": f"chunk {sequence}"},
            }
            for sequence in sequences
        ]
    }


# ------------------------------------------------------------------ pairing


async def test_a_pairing_code_is_exchanged_for_a_secret_and_the_socket_closes():
    link = Link(socket=FakeSocket(headers={})).start()
    frame_id = link.socket.send(
        "pair",
        {"pairing_code": PAIRING_CODE, "display_name": "laptop", "hello": hello()},
    )

    paired = await link.socket.answer_to(frame_id)

    assert paired["type"] == "paired"
    assert paired["body"]["host_secret"] == SECRET
    assert paired["body"]["host_id"] == str(link.store.host_id)
    assert (await link.ended())[0] == LinkCloseCode.NORMAL


async def test_a_refused_pairing_code_is_a_credential_failure():
    link = Link(socket=FakeSocket(headers={})).start()
    frame_id = link.socket.send(
        "pair",
        {"pairing_code": "x" * 20, "display_name": "laptop", "hello": hello()},
    )

    error = await link.socket.answer_to(frame_id)

    assert error["type"] == "error"
    assert error["body"]["code"] == "UNAUTHORIZED"
    assert (await link.ended())[0] == LinkCloseCode.INVALID_CREDENTIAL


async def test_the_first_frame_must_be_pair_or_hello():
    link = Link().start()
    link.socket.send("control", {})

    assert (await link.ended())[0] == LinkCloseCode.PROTOCOL_VIOLATION


async def test_a_frame_that_is_not_json_text_is_a_protocol_violation():
    link = Link().start()
    link.socket.send_raw({"type": "websocket.receive", "bytes": b"\x00"})

    assert (await link.ended())[0] == LinkCloseCode.PROTOCOL_VIOLATION


# ------------------------------------------------------------------- hello


@pytest.mark.parametrize(
    "headers",
    [{}, {"authorization": "Basic abc"}, {"authorization": "Bearer "}],
    ids=["missing", "wrong-scheme", "empty"],
)
async def test_a_missing_or_malformed_credential_closes_4403(headers):
    link = Link(socket=FakeSocket(headers=headers)).start()
    link.socket.send("hello", {"hello": hello()})

    assert (await link.ended())[0] == LinkCloseCode.INVALID_CREDENTIAL


async def test_an_unknown_secret_closes_4401_with_the_one_reason():
    link = Link(socket=FakeSocket(headers={"authorization": "Bearer nope"})).start()
    link.socket.send("hello", {"hello": hello()})

    assert await link.ended() == (
        LinkCloseCode.REVOKED_OR_MISSING,
        REVOKED_OR_MISSING_REASON,
    )


async def test_a_revoked_host_reads_exactly_like_an_unknown_one():
    store = FakeStore()
    store.revoked = True
    link = Link(store=store).start()
    link.socket.send("hello", {"hello": hello()})

    assert await link.ended() == (
        LinkCloseCode.REVOKED_OR_MISSING,
        REVOKED_OR_MISSING_REASON,
    )


async def test_an_old_protocol_is_told_to_upgrade():
    link = Link().start()
    link.socket.send("hello", {"hello": hello(protocol_version=2)})

    assert (await link.ended())[0] == LinkCloseCode.UPGRADE_REQUIRED


async def test_welcome_names_the_host_and_the_heartbeat():
    link = Link()
    welcome = await link.open()

    server_time = datetime.fromisoformat(welcome["body"].pop("server_time"))
    assert welcome["body"] == {
        "host_id": str(link.store.host_id),
        "user_id": str(link.store.user_id),
        "protocol_version": 3,
        "heartbeat_ms": 20_000,
        # The host resends a named tools/call after a drop only when told so.
        "idempotent_tool_calls": True,
    }
    # The host corrects its command-expiry checks by this; it is UTC and now.
    assert server_time.utcoffset() == timedelta(0)
    assert abs(datetime.now(timezone.utc) - server_time) < timedelta(seconds=5)
    assert len(link.registry) == 1
    await link.close()
    assert len(link.registry) == 0


async def test_a_second_hello_is_a_protocol_violation():
    link = Link()
    await link.open()
    link.socket.send("hello", {"hello": hello()})

    assert (await link.ended())[0] == LinkCloseCode.PROTOCOL_VIOLATION


# ----------------------------------------------------------------- control


async def test_control_applies_every_update_and_answers_with_commands():
    link = Link()
    queued = command()
    link.store.queue.append(queued)
    await link.open()
    checkpoint = {"run_id": str(uuid7()), "lease_epoch": 1, "state": "RUNNING"}

    frame_id = link.socket.send(
        "control",
        {
            "capacity": {"max_runs": 2, "active_runs": 1, "available_runs": 1},
            "acknowledged_command_ids": [],
            "checkpoints": [checkpoint],
            "rejections": [],
        },
    )
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "control_ok"
    assert [c["command_id"] for c in answer["body"]["commands"]] == [
        str(queued.command_id)
    ]
    assert answer["body"]["refused"] == []
    (applied,) = link.store.applied
    assert [str(c.run_id) for c in applied.checkpoints] == [checkpoint["run_id"]]
    assert applied.capacity.available_runs == 1
    await link.close()


async def test_a_malformed_update_is_refused_by_name_and_the_rest_still_apply():
    """One bad item used to fail the whole poll, and every command with it."""
    link = Link()
    await link.open()
    good_ack = uuid7()
    good = {"run_id": str(uuid7()), "lease_epoch": 1, "state": "RUNNING"}
    bad = {"run_id": str(uuid7()), "lease_epoch": 0, "state": "RUNNING"}
    bad_rejection = {"command_id": str(uuid7()), "code": "NOT_A_CODE"}

    frame_id = link.socket.send(
        "control",
        {
            "acknowledged_command_ids": [str(good_ack), "not-a-uuid"],
            "checkpoints": [bad, good],
            "rejections": [bad_rejection],
        },
    )
    answer = await link.socket.answer_to(frame_id)

    refused = {(r["kind"], r["index"]) for r in answer["body"]["refused"]}
    assert refused == {("ack", 1), ("checkpoint", 0), ("rejection", 0)}
    by_kind = {r["kind"]: r for r in answer["body"]["refused"]}
    assert by_kind["checkpoint"]["run_id"] == bad["run_id"]
    assert by_kind["rejection"]["command_id"] == bad_rejection["command_id"]
    (applied,) = link.store.applied
    assert applied.acknowledged_command_ids == [good_ack]
    assert [str(c.run_id) for c in applied.checkpoints] == [good["run_id"]]
    await link.close()


async def test_a_host_revoked_between_frames_is_closed_4401():
    link = Link()
    await link.open()
    link.store.control_error = AgentHostProtocolViolation("Agent Host is revoked")
    link.socket.send("control", {})

    assert (await link.ended())[0] == LinkCloseCode.REVOKED_OR_MISSING


async def test_an_invalid_capacity_is_an_invalid_frame_not_a_closed_link():
    link = Link()
    await link.open()
    frame_id = link.socket.send("control", {"capacity": {"max_runs": 1000}})

    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "INVALID_FRAME"
    assert link.task is not None and not link.task.done()
    await link.close()


# ------------------------------------------------------------------ events


async def test_an_event_batch_is_acknowledged_through_its_last_sequence():
    link = Link()
    await link.open()
    run_id = uuid7()

    frame_id = link.socket.send("events", _events_body(run_id, (4, 5, 6)))
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "events_ok"
    assert answer["body"]["ack"] == {
        "run_id": str(run_id),
        "lease_epoch": 1,
        "acked_through": 6,
    }
    await link.close()


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (AgentHostNotFound("run lease does not belong to this host"), "NOT_FOUND"),
        (AgentHostStaleLease("stale run lease epoch"), "STALE_LEASE"),
        (AgentHostSequenceGap("event sequence gap: expected 3, got 5"), "SEQUENCE_GAP"),
        (AgentHostTerminalRun("terminal run cannot accept events"), "TERMINAL_RUN"),
        (AgentHostProtocolViolation("something else"), "INVALID_FRAME"),
    ],
)
async def test_each_refused_batch_names_its_reason(failure, code):
    link = Link()
    await link.open()
    link.store.append_error = failure

    frame_id = link.socket.send("events", _events_body())
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "error"
    assert answer["body"] == {"code": code, "message": str(failure), "retryable": False}
    await link.close()


async def test_a_batch_that_is_not_contiguous_is_an_invalid_frame():
    link = Link()
    await link.open()

    frame_id = link.socket.send("events", _events_body(sequences=(1, 3)))
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "INVALID_FRAME"
    assert link.store.appended == []
    await link.close()


async def test_a_bug_in_a_handler_is_answered_internal_and_the_link_survives():
    link = Link()
    await link.open()
    link.store.append_error = ZeroDivisionError("a bug")

    frame_id = link.socket.send("events", _events_body())
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "INTERNAL"
    assert "a bug" not in answer["body"]["message"]
    link.store.append_error = None
    retried = link.socket.send("events", _events_body())
    assert (await link.socket.answer_to(retried))["type"] == "events_ok"
    await link.close()


async def test_a_transient_failure_is_retryable():
    link = Link()
    await link.open()
    link.store.append_error = ConnectionResetError("redis went away")

    frame_id = link.socket.send("events", _events_body())
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "UNAVAILABLE"
    assert answer["body"]["retryable"] is True
    await link.close()


async def test_an_unknown_frame_type_is_answered_and_the_link_stays_up():
    link = Link()
    await link.open()

    frame_id = link.socket.send("teleport", {})
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "INVALID_FRAME"
    assert link.task is not None and not link.task.done()
    await link.close()


# ------------------------------------------------------- harnesses, revoke


async def test_published_harnesses_come_back_as_stored():
    link = Link()
    await link.open()
    stale_after = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    frame_id = link.socket.send(
        "harnesses",
        {
            "harnesses": [
                {
                    "harness_key": "Claude_Code",
                    "display_name": "Claude Code",
                    "adapter_version": "1.0.0",
                    "health": "READY",
                    "config_revision": "rev-1",
                    "stale_after": stale_after,
                }
            ]
        },
    )
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "harnesses_ok"
    (item,) = answer["body"]["items"]
    assert item["harness_key"] == "claude-code"
    assert item["host_id"] == str(link.store.host_id)
    await link.close()


async def test_a_host_can_retire_its_own_credential():
    link = Link()
    await link.open()

    frame_id = link.socket.send("revoke")
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "revoked"
    assert (await link.ended())[0] == LinkCloseCode.NORMAL
    assert link.store.revoked is True


# --------------------------------------------------------------------- mcp


def _mcp_body(link: Link, method: str, **extra) -> dict:
    return {
        "run_id": str(uuid7()),
        "conversation_id": str(link.conversation_id),
        "token": TOKEN,
        "method": method,
        **extra,
    }


async def test_tools_are_listed_for_an_authorized_run():
    link = Link()
    await link.open()

    frame_id = link.socket.send("mcp", _mcp_body(link, "tools/list"))
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "mcp_ok"
    (tool,) = answer["body"]["result"]["tools"]
    assert tool["name"] == "lemma_pod_get_records"
    assert tool["inputSchema"] == {"type": "object"}
    await link.close()


async def test_a_tool_call_returns_the_mcp_result():
    link = Link()
    await link.open()

    frame_id = link.socket.send(
        "mcp",
        _mcp_body(
            link,
            "tools/call",
            params={"name": "lemma_pod_get_records", "arguments": {"table": "t"}},
        ),
    )
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["result"]["structuredContent"] == {"success": True}
    assert answer["body"]["result"]["isError"] is False
    assert link.mcp_service.calls == [("lemma_pod_get_records", {"table": "t"})]
    await link.close()


@pytest.mark.parametrize("which", ["token", "conversation"])
async def test_a_token_that_does_not_grant_the_conversation_is_unauthorized(which):
    link = Link()
    await link.open()
    body = _mcp_body(link, "tools/list")
    if which == "token":
        body["token"] = "someone-elses"
    else:
        body["conversation_id"] = str(uuid7())

    frame_id = link.socket.send("mcp", body)
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "UNAUTHORIZED"
    assert link.mcp_service.calls == []
    await link.close()


async def test_a_slow_tool_call_does_not_hold_up_the_heartbeat():
    link = Link()
    await link.open()
    link.mcp_service.release_call.clear()

    slow = link.socket.send(
        "mcp",
        _mcp_body(
            link,
            "tools/call",
            params={"name": "lemma_pod_get_records", "arguments": {}},
        ),
    )
    heartbeat = link.socket.send("control", {})

    assert (await link.socket.answer_to(heartbeat))["type"] == "control_ok"
    link.mcp_service.release_call.set()
    assert (await link.socket.answer_to(slow))["type"] == "mcp_ok"
    await link.close()


async def test_past_the_in_flight_bound_a_request_is_refused_not_queued():
    """Queueing would stop the reader, and the heartbeat behind it."""
    link = Link(max_in_flight=1)
    await link.open()
    link.mcp_service.release_call.clear()
    call = {"name": "lemma_pod_get_records", "arguments": {}}

    first = link.socket.send("mcp", _mcp_body(link, "tools/call", params=call))
    second = link.socket.send("mcp", _mcp_body(link, "tools/call", params=call))
    refused = await link.socket.answer_to(second)
    heartbeat = link.socket.send("control", {})

    assert refused["body"]["code"] == "UNAVAILABLE"
    assert refused["body"]["retryable"] is True
    assert (await link.socket.answer_to(heartbeat))["type"] == "control_ok"
    link.mcp_service.release_call.set()
    assert (await link.socket.answer_to(first))["type"] == "mcp_ok"
    await link.close()


# -------------------------------------------------------- interaction_wait


def _wait_body(link: Link, tool_call_id: str) -> dict:
    return {
        "run_id": str(uuid7()),
        "conversation_id": str(link.conversation_id),
        "token": TOKEN,
        "tool_call_id": tool_call_id,
    }


async def test_an_interaction_already_decided_is_answered_at_once():
    link = Link()
    await link.open()
    link.mcp_service.answers["call-1"] = {"answers": {"Pick": "Blue"}}

    frame_id = link.socket.send("interaction_wait", _wait_body(link, "call-1"))
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "interaction_ok"
    assert answer["body"]["answer"] == {"answers": {"Pick": "Blue"}}
    await link.close()


async def test_a_parked_interaction_is_answered_on_the_push():
    """The floor is a minute here, so only the published frame can wake it."""
    link = Link(recheck_seconds=60.0)
    await link.open()
    frame_id = link.socket.send("interaction_wait", _wait_body(link, "call-2"))
    channel = conversation_channel(link.conversation_id)
    for _ in range(100):
        if link.channels.subscribers(channel):
            break
        await asyncio.sleep(0.01)

    # Someone else's news is not a reason to answer.
    await link.channels.publish(channel, {"type": "token", "data": "hi"})
    link.mcp_service.answers["call-2"] = {"approved": True}
    await link.channels.publish(
        channel, {"type": "message", "data": {"tool_call_id": "call-2"}}
    )
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["answer"] == {"approved": True}
    await link.close()


async def test_a_lost_push_is_caught_by_the_floor():
    link = Link(recheck_seconds=0.05)
    await link.open()
    frame_id = link.socket.send("interaction_wait", _wait_body(link, "call-3"))
    await asyncio.sleep(0.1)

    link.mcp_service.answers["call-3"] = {"approved": False}
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["answer"] == {"approved": False}
    await link.close()


async def test_waiting_is_authorized_like_a_tool_call():
    link = Link()
    await link.open()
    body = _wait_body(link, "call-4")
    body["token"] = "nope"

    frame_id = link.socket.send("interaction_wait", body)
    answer = await link.socket.answer_to(frame_id)

    assert answer["body"]["code"] == "UNAUTHORIZED"
    await link.close()


# ---------------------------------------------------------------- pushing


async def _subscribed(link: Link) -> None:
    channel = host_poke_channel(link.store.host_id)
    for _ in range(100):
        if link.channels.subscribers(channel):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the link never subscribed to its host channel")


async def test_a_poke_pushes_the_queued_command_at_once():
    link = Link(push_floor_seconds=60.0)
    await link.open()
    await _subscribed(link)
    queued = command(AgentHostCommandKind.CANCEL_RUN)
    link.store.queue.append(queued)

    await link.channels.publish(host_poke_channel(link.store.host_id), {"type": "poke"})
    pushed = await link.socket.frame()

    assert pushed["type"] == "commands"
    assert "re" not in pushed and "id" not in pushed
    assert [c["command_id"] for c in pushed["body"]["commands"]] == [
        str(queued.command_id)
    ]
    await link.close()


async def test_the_floor_pushes_without_a_poke_and_does_not_repeat_itself():
    link = Link(push_floor_seconds=0.02, resend_after_seconds=30.0)
    await link.open()
    link.store.queue.append(command())

    pushed = await link.socket.frame()
    reads = link.store.reads
    while link.store.reads < reads + 5:
        await asyncio.sleep(0.02)

    assert pushed["type"] == "commands"
    assert link.socket.nothing_sent(), "an unacknowledged command was pushed twice"
    await link.close()


async def test_an_unacknowledged_command_is_pushed_again_after_the_window():
    link = Link(push_floor_seconds=0.02, resend_after_seconds=0.05)
    await link.open()
    queued = command()
    link.store.queue.append(queued)

    first = await link.socket.frame()
    second = await link.socket.frame()

    assert first["body"] == second["body"]
    await link.close()


async def test_a_command_answered_in_control_ok_is_not_pushed_again():
    link = Link(push_floor_seconds=0.02)
    link.store.queue.append(command())
    await link.open()

    frame_id = link.socket.send("control", {})
    answer = await link.socket.answer_to(frame_id)
    reads = link.store.reads
    while link.store.reads < reads + 3:
        await asyncio.sleep(0.02)

    assert len(answer["body"]["commands"]) == 1
    assert link.socket.nothing_sent()
    await link.close()


# --------------------------------------------------- one socket per host


async def test_a_newer_link_for_the_host_closes_the_older_one():
    store, channels, registry = FakeStore(), FakeChannels(), AgentHostLinkRegistry()
    older = Link(store=store, channels=channels, registry=registry)
    await older.open()
    await _subscribed(older)

    newer = Link(store=store, channels=channels, registry=registry)
    await newer.open()

    assert (await older.ended())[0] == LinkCloseCode.SUPERSEDED
    assert newer.task is not None and not newer.task.done()
    assert len(registry) == 1
    await newer.close()


async def test_a_link_does_not_supersede_itself():
    link = Link(push_floor_seconds=0.02)
    await link.open()
    await _subscribed(link)
    await asyncio.sleep(0.1)

    assert link.task is not None and not link.task.done()
    await link.close()


async def _held(channels: FakeChannels, generation: int) -> None:
    """Wait until the link that claimed ``generation`` is blocked announcing it."""
    for _ in range(200):
        if generation in channels.held:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"generation {generation} never announced")


@pytest.mark.parametrize("announced_first", ["older", "newer"])
async def test_racing_handshakes_leave_exactly_the_newer_link(announced_first):
    """Both subscribe before either announces; each then hears the other.

    Deciding "newer" by connection id closed both, because each saw a
    connection id that was not its own. The generation each hello claimed is
    the order, whichever announcement lands first.
    """
    store, registry = FakeStore(), AgentHostLinkRegistry()
    channels = FakeChannels(hold_announcements=True)
    older = Link(store=store, channels=channels, registry=registry)
    await older.open()
    await _held(channels, 1)  # subscribed, checked ownership, now announcing
    newer = Link(store=store, channels=channels, registry=registry)
    await newer.open()
    await _held(channels, 2)
    assert channels.subscribers(host_poke_channel(store.host_id)) == 2

    order = [1, 2] if announced_first == "older" else [2, 1]
    for generation in order:
        channels.held[generation].set()
        await asyncio.sleep(0.05)

    assert (await older.ended())[0] == LinkCloseCode.SUPERSEDED
    await asyncio.sleep(0.1)
    assert newer.task is not None and not newer.task.done()
    assert newer.socket.closed is None
    assert len(registry) == 1
    await newer.close()


async def test_a_newer_hello_announced_before_this_link_subscribed_still_wins():
    """The newer link's notice went out while nobody here was listening.

    It claimed its generation before announcing, so the read this link takes
    once subscribed sees it -- and this link closes without announcing, so it
    cannot unseat the newer one either.
    """
    channels = FakeChannels()
    channels.subscribe_gate = asyncio.Event()
    older = Link(channels=channels)
    await older.open()
    # A newer hello elsewhere: claimed, announced, heard by no one.
    older.store.generation += 1
    channels.subscribe_gate.set()

    assert (await older.ended())[0] == LinkCloseCode.SUPERSEDED
    assert not any(
        isinstance(message, dict) and message.get("type") == "superseded"
        for _, message in channels.published
    )


async def test_a_notice_without_a_generation_supersedes_nothing():
    """An unreadable or pre-generation notice is not a reason to close."""
    link = Link()
    await link.open()
    await _subscribed(link)

    await link.channels.publish(
        host_poke_channel(link.store.host_id),
        {"type": "superseded", "connection_id": str(uuid7())},
    )
    await asyncio.sleep(0.05)

    assert link.task is not None and not link.task.done()
    await link.close()


async def test_revoking_the_host_closes_its_live_link():
    link = Link()
    await link.open()
    await _subscribed(link)

    await link.channels.publish(
        host_poke_channel(link.store.host_id), {"type": "revoked"}
    )

    assert await link.ended() == (
        LinkCloseCode.REVOKED_OR_MISSING,
        REVOKED_OR_MISSING_REASON,
    )


# ---------------------------------------------------------------- liveness


async def test_silence_past_three_heartbeats_closes_4408():
    link = Link(heartbeat_ms=30)
    await link.open()

    assert (await link.ended(timeout=1.0))[0] == LinkCloseCode.HEARTBEAT_TIMEOUT


async def test_frames_keep_the_link_alive():
    link = Link(heartbeat_ms=60)
    await link.open()
    for _ in range(6):
        await asyncio.sleep(0.05)
        frame_id = link.socket.send("control", {})
        await link.socket.answer_to(frame_id)

    assert link.task is not None and not link.task.done()
    await link.close()


async def test_the_host_going_away_ends_the_session_without_a_close_frame():
    link = Link()
    await link.open()
    link.socket.hang_up()

    assert await link.ended() is None


async def test_a_drain_sends_reconnect_then_closes_1012():
    registry = AgentHostLinkRegistry()
    links = [Link(registry=registry, store=FakeStore()) for _ in range(2)]
    for link in links:
        await link.open()

    await registry.drain(timeout=2.0)

    for link in links:
        reconnect = await link.socket.frame()
        assert reconnect["type"] == "reconnect"
        assert 0 <= reconnect["body"]["after_ms"] <= 5_000
        assert link.socket.closed == (LinkCloseCode.RESTARTING, "restarting")
    assert len(registry) == 0
