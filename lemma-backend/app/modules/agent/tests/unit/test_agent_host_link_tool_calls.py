"""A relayed tool call executes at most once, and a dropped link does not end it.

The host names each ``tools/call`` with a ``request_id`` and reuses it on every
retry, on any link. These drive the session the way a host would across drops
and retries, with a real (in-memory) Redis behind the record, and count how
often the tool actually ran. Then the two request kinds that are not tool
calls: parked waits get their own slots and end with their run, and a run
named by a request has to belong to its conversation.
"""

from __future__ import annotations

import asyncio
from uuid import uuid7

from fakeredis import aioredis as fake_aioredis

from app.modules.agent.services.agent_host_link_tool_calls import ToolCallLedger
from app.modules.agent.services.realtime import conversation_channel
from app.modules.agent.tests.unit.agent_host_link_fakes import (
    TOKEN,
    FakeConversationMcp,
    Link,
)

CALL = {"name": "lemma_pod_get_records", "arguments": {"table": "t"}}


def _call(link: Link, *, run_id, request_id: str | None = "call-1") -> dict:
    body = {
        "run_id": str(run_id),
        "conversation_id": str(link.conversation_id),
        "token": TOKEN,
        "method": "tools/call",
        "params": CALL,
    }
    if request_id is not None:
        body["request_id"] = request_id
    return body


async def _until(predicate, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never held")


# ----------------------------------------------------------- at most once


async def test_a_retried_call_is_answered_from_its_record_not_run_again():
    link = Link()
    await link.open()
    run_id = uuid7()

    first = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=run_id))
    )
    again = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=run_id))
    )

    assert first["type"] == again["type"] == "mcp_ok"
    assert again["body"] == first["body"]
    assert link.mcp_service.calls == [("lemma_pod_get_records", {"table": "t"})]
    # Another request id is another call.
    other = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=run_id, request_id="call-2"))
    )
    assert other["type"] == "mcp_ok"
    assert len(link.mcp_service.calls) == 2
    await link.close()


async def test_a_duplicate_while_the_call_runs_waits_for_its_outcome():
    link = Link()
    await link.open()
    run_id = uuid7()
    link.mcp_service.release_call.clear()

    first = link.socket.send("mcp", _call(link, run_id=run_id))
    await _until(lambda: link.mcp_service.calls)
    duplicate = link.socket.send("mcp", _call(link, run_id=run_id))
    await asyncio.sleep(0.3)
    link.mcp_service.release_call.set()

    answers = [await link.socket.answer_to(f) for f in (first, duplicate)]
    assert [a["type"] for a in answers] == ["mcp_ok", "mcp_ok"]
    assert answers[0]["body"] == answers[1]["body"]
    assert len(link.mcp_service.calls) == 1
    await link.close()


async def test_a_link_that_drops_mid_call_does_not_cancel_it_and_the_retry_gets_it():
    ledger = ToolCallLedger(fake_aioredis.FakeRedis(decode_responses=True))
    first = Link(ledger=ledger)
    service = first.mcp_service
    await first.open()
    run_id = uuid7()
    service.release_call.clear()

    first.socket.send("mcp", _call(first, run_id=run_id))
    await _until(lambda: service.calls)
    await first.close()  # the host's network drops; the call is still running

    # The host reconnects -- possibly to another replica -- and retries.
    second = Link(
        ledger=ledger,
        mcp_service=service,
        conversation_id=first.conversation_id,
    )
    await second.open()
    retry = second.socket.send("mcp", _call(second, run_id=run_id))
    await asyncio.sleep(0.1)
    service.release_call.set()
    answer = await second.socket.answer_to(retry)

    assert answer["type"] == "mcp_ok"
    assert answer["body"]["result"]["structuredContent"] == {"success": True}
    assert len(service.calls) == 1
    await second.close()


async def test_a_call_that_finished_after_its_link_closed_is_answered_from_the_record():
    ledger = ToolCallLedger(fake_aioredis.FakeRedis(decode_responses=True))
    first = Link(ledger=ledger)
    service = first.mcp_service
    await first.open()
    run_id = uuid7()
    service.release_call.clear()
    first.socket.send("mcp", _call(first, run_id=run_id))
    await _until(lambda: service.calls)
    await first.close()
    service.release_call.set()
    await asyncio.sleep(0.05)

    second = Link(
        ledger=ledger, mcp_service=service, conversation_id=first.conversation_id
    )
    await second.open()
    answer = await second.socket.answer_to(
        second.socket.send("mcp", _call(second, run_id=run_id))
    )

    assert answer["type"] == "mcp_ok"
    assert len(service.calls) == 1
    await second.close()


# ------------------------------------------- failures after dispatch are final


async def test_a_bug_after_dispatch_is_internal_and_never_retryable():
    link = Link()
    await link.open()
    run_id = uuid7()
    link.mcp_service.call_error = ZeroDivisionError("a bug")

    answer = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=run_id))
    )
    assert answer["type"] == "error"
    assert answer["body"]["code"] == "INTERNAL"
    assert answer["body"]["retryable"] is False
    assert "a bug" not in answer["body"]["message"]

    # A retry anyway is told the same, and the tool does not run again.
    again = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=run_id))
    )
    assert again["body"]["code"] == "INTERNAL"
    assert again["body"]["retryable"] is False
    assert len(link.mcp_service.calls) == 1
    await link.close()


async def test_an_outage_after_dispatch_is_unavailable_and_never_retryable():
    link = Link()
    await link.open()
    link.mcp_service.call_error = ConnectionResetError("redis went away")

    answer = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=uuid7()))
    )
    assert answer["body"]["code"] == "UNAVAILABLE"
    assert answer["body"]["retryable"] is False
    await link.close()


async def test_an_unnamed_call_is_also_never_retryable_once_dispatched():
    """A host too old to send request ids still must not run a call twice."""
    link = Link()
    await link.open()
    link.mcp_service.call_error = ZeroDivisionError("a bug")

    answer = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=uuid7(), request_id=None))
    )
    assert answer["body"]["code"] == "INTERNAL"
    assert answer["body"]["retryable"] is False
    await link.close()


async def test_a_refusal_before_dispatch_stays_retryable():
    service_link = Link()  # the auth lookup fails before anything is dispatched
    await service_link.open()

    async def unreachable(**_kwargs) -> bool:
        raise ConnectionResetError("the database went away")

    service_link.mcp_service.authorize = unreachable
    answer = await service_link.socket.answer_to(
        service_link.socket.send("mcp", _call(service_link, run_id=uuid7()))
    )
    assert answer["body"]["code"] == "UNAVAILABLE"
    assert answer["body"]["retryable"] is True
    assert service_link.mcp_service.calls == []
    await service_link.close()


async def test_a_malformed_request_id_is_an_invalid_frame():
    link = Link()
    await link.open()
    answer = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=uuid7(), request_id="no spaces"))
    )
    assert answer["body"]["code"] == "INVALID_FRAME"
    assert link.mcp_service.calls == []
    await link.close()


# ------------------------------------------------ the run must be this chat's


async def test_a_run_of_another_conversation_is_unauthorized():
    link = Link()
    await link.open()
    foreign = uuid7()
    link.mcp_service.foreign_runs.add(foreign)

    answer = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=foreign))
    )
    assert answer["body"]["code"] == "UNAUTHORIZED"
    assert link.mcp_service.calls == []
    await link.close()


# ------------------------------------------------------------ parked waits


def _wait(link: Link, *, run_id, tool_call_id: str) -> dict:
    return {
        "run_id": str(run_id),
        "conversation_id": str(link.conversation_id),
        "token": TOKEN,
        "tool_call_id": tool_call_id,
    }


async def test_parked_waits_do_not_take_the_tool_calls_slots():
    link = Link(max_in_flight=1, max_waits=1, recheck_seconds=60.0)
    await link.open()

    parked = link.socket.send(
        "interaction_wait", _wait(link, run_id=uuid7(), tool_call_id="w1")
    )
    await _until(
        lambda: link.channels.subscribers(conversation_channel(link.conversation_id))
    )
    call = await link.socket.answer_to(
        link.socket.send("mcp", _call(link, run_id=uuid7()))
    )
    assert call["type"] == "mcp_ok"

    # The waits' own pool is full, and says so.
    refused = await link.socket.answer_to(
        link.socket.send(
            "interaction_wait", _wait(link, run_id=uuid7(), tool_call_id="w2")
        )
    )
    assert refused["body"]["code"] == "UNAVAILABLE"
    assert refused["body"]["retryable"] is True

    link.mcp_service.answers["w1"] = {"approved": True}
    await link.channels.publish(
        conversation_channel(link.conversation_id), {"data": {"tool_call_id": "w1"}}
    )
    assert (await link.socket.answer_to(parked))["type"] == "interaction_ok"
    await link.close()


async def test_a_wait_ends_when_its_run_does():
    link = Link(recheck_seconds=0.05)
    await link.open()
    run_id = uuid7()
    frame_id = link.socket.send(
        "interaction_wait", _wait(link, run_id=run_id, tool_call_id="w3")
    )
    await asyncio.sleep(0.1)

    link.mcp_service.ended_runs.add(run_id)
    answer = await link.socket.answer_to(frame_id)

    assert answer["type"] == "error"
    assert answer["body"]["code"] == "TERMINAL_RUN"
    assert answer["body"]["retryable"] is False
    await link.close()


async def test_a_decided_wait_is_answered_even_if_the_run_then_ended():
    link = Link()
    await link.open()
    run_id = uuid7()
    link.mcp_service.answers["w4"] = {"approved": True}
    link.mcp_service.ended_runs.add(run_id)

    answer = await link.socket.answer_to(
        link.socket.send(
            "interaction_wait", _wait(link, run_id=run_id, tool_call_id="w4")
        )
    )
    assert answer["type"] == "interaction_ok"
    await link.close()


def test_the_fake_service_counts_as_the_real_ones_shape():
    """Keeps the stand-in honest about the one new keyword it must accept."""
    import inspect

    from app.modules.agent.services.conversation_mcp_service import (
        ConversationMCPService,
    )

    real = inspect.signature(ConversationMCPService.authorize).parameters
    fake = inspect.signature(FakeConversationMcp.authorize).parameters
    assert set(real) == set(fake)
    assert hasattr(ConversationMCPService, "run_has_ended")
