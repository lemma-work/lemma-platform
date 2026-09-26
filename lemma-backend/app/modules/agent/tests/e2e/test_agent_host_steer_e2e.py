"""Talking to a local coding agent while it is working.

A message sent while an Agent Host run is in flight is queued. For a harness
that advertises ACP steering (the pinned Claude Code and Codex adapters), Lemma
hands it to the host as a ``STEER_RUN`` and the host adds it to the running
turn; for one that does not, it waits, and the follow-up turn delivers it the
moment the current one ends. Until something delivers it, the person can take
it back.

Only the provider is scripted, by ``scripted_acp_agent.py`` replaying
``scenarios/steer*.json``; the Rust host, the link, dispatch, the harness and the
follow-up turn are all real.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, Field

from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tests.e2e.test_agent_host_process_e2e import (
    AcpRecord,
    PairingCode,
    StreamFrame,
    create_host_conversation,
    running_host,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.local_cli, pytest.mark.approval_worker]


class Message(BaseModel):
    id: UUID
    role: str
    kind: str
    text: str | None = None
    agent_run_id: UUID | None = None
    metadata: JsonObject = Field(default_factory=dict)


class Messages(BaseModel):
    items: list[Message]


class Started(BaseModel):
    agent_run_id: UUID
    started_new_run: bool


async def _paired(scenario: E2EScenario) -> PairingCode:
    await scenario.create_org_with_pod(name_prefix="Host steering")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "isolated steering host"},
    )
    assert minted.is_success, minted.text
    return PairingCode.model_validate(minted.json())


async def _messages(client: httpx.AsyncClient, conversation: str) -> list[Message]:
    response = await client.get(f"{conversation}/messages")
    assert response.is_success, response.text
    return Messages.model_validate(response.json()).items


def _traffic(path: Path) -> list[AcpRecord]:
    return [
        AcpRecord.model_validate_json(line) for line in path.read_text().splitlines()
    ]


def _sent(path: Path, method: str) -> list[AcpRecord]:
    return [
        record
        for record in _traffic(path)
        if record.direction == "client->agent" and record.message.method == method
    ]


async def _turn(
    client: httpx.AsyncClient,
    conversation: str,
    content: str,
    *,
    when_text_contains: str,
    meanwhile,
) -> tuple[str, list[StreamFrame]]:
    """Send a message, and do ``meanwhile`` once the agent is visibly working."""
    live_text = ""
    frames: list[StreamFrame] = []
    acted = False
    async with asyncio.timeout(90):
        async with client.stream(
            "POST", f"{conversation}/messages", json={"content": content}
        ) as response:
            assert response.is_success, await response.aread()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                frame = StreamFrame.model_validate_json(line.removeprefix("data: "))
                frames.append(frame)
                if frame.type == "token" and frame.kind == "text":
                    assert isinstance(frame.data, str)
                    live_text += frame.data
                    if not acted and when_text_contains in live_text:
                        acted = True
                        await meanwhile()
                if frame.type in {"completed", "error", "stopped"}:
                    break
    assert acted, f"the agent never said {when_text_contains!r}: {live_text!r}"
    return live_text, frames


async def _nothing() -> None:
    return None


async def _append(
    client: httpx.AsyncClient, conversation: str, content: str
) -> Started:
    response = await client.post(
        f"{conversation}/messages/append", json={"content": content}
    )
    assert response.is_success, response.text
    return Started.model_validate(response.json())


@pytest.mark.asyncio
async def test_a_steerable_agent_hears_a_message_within_the_turn_it_was_sent_to(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
) -> None:
    del worker
    pairing = await _paired(scenario)
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path, base_url, pairing.pairing_code, scenario_file="steer.json"
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            conversation = await create_host_conversation(client, scenario)
            steered: list[Started] = []

            async def steer() -> None:
                steered.append(
                    await _append(client, conversation, "Also update the changelog.")
                )

            live_text, frames = await _turn(
                client,
                conversation,
                "Start the first task.",
                when_text_contains="Working on the first request",
                meanwhile=steer,
            )

            assert frames[-1].type == "completed", frames[-1]
            [joined] = steered
            assert not joined.started_new_run, "a mid-turn message joins that turn"
            # The scenario ends its turn only after a steer arrives, and echoes
            # what it was told, so this is the agent's own account of it.
            assert "Also noted: " in live_text
            assert "Also update the changelog." in live_text
            assert len(_sent(traffic, "session/prompt")) == 1
            assert len(_sent(traffic, "_session/steering")) == 1

            messages = await _messages(client, conversation)
            [message] = [
                item for item in messages if item.text == "Also update the changelog."
            ]
            # Claimed by the run it reached, which is also why no follow-up
            # turn answers it a second time.
            assert message.metadata.get("steered_into_run") == str(joined.agent_run_id)
            withdrawn = await client.delete(f"{conversation}/messages/{message.id}")
            assert withdrawn.status_code == 409, withdrawn.text


@pytest.mark.asyncio
async def test_a_message_for_an_agent_that_cannot_steer_is_the_next_turn(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
) -> None:
    del worker
    pairing = await _paired(scenario)
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path,
        base_url,
        pairing.pairing_code,
        scenario_file="steer-unsupported.json",
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            conversation = await create_host_conversation(client, scenario)

            async def queue_two() -> None:
                await _append(client, conversation, "First queued.")
                await _append(client, conversation, "Second queued.")
                # Only now may the turn finish, so both provably arrived mid-turn.
                traffic.with_suffix(".release").write_text("continue")

            _live_text, frames = await _turn(
                client,
                conversation,
                "Start the first task.",
                when_text_contains="Working without steering",
                meanwhile=queue_two,
            )
            assert frames[-1].type == "completed", frames[-1]

            async def prompted() -> list[AcpRecord]:
                return _sent(traffic, "session/prompt")

            prompts = await eventually(
                label="the follow-up turn for what was queued",
                probe=prompted,
                done=lambda sent: len(sent) == 2,
                timeout_seconds=60,
            )
            # One follow-up for both, carrying both, in the order they were sent.
            follow_up = str(prompts[1].message.params)
            assert "First queued." in follow_up
            assert "Second queued." in follow_up
            assert follow_up.index("First queued.") < follow_up.index("Second queued.")
            assert not _sent(traffic, "_session/steering"), (
                "an agent that did not advertise steering was sent the extension"
            )

            async def answered() -> list[Message]:
                return await _messages(client, conversation)

            messages = await eventually(
                label="the follow-up turn's answer",
                probe=answered,
                done=lambda items: (
                    len([item for item in items if item.role == "assistant"]) == 2
                ),
                timeout_seconds=60,
            )
            queued = [
                item
                for item in messages
                if item.text in {"First queued.", "Second queued."}
            ]
            claimed_by = {item.metadata.get("steered_into_run") for item in queued}
            answer_runs = {
                str(item.agent_run_id) for item in messages if item.role == "assistant"
            }
            assert len(claimed_by) == 1
            assert claimed_by <= answer_runs, "claimed by the turn that answered"
            assert claimed_by != {str(queued[0].agent_run_id)}


@pytest.mark.asyncio
async def test_a_queued_message_can_be_taken_back_before_anyone_reads_it(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
) -> None:
    del worker
    pairing = await _paired(scenario)
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path,
        base_url,
        pairing.pairing_code,
        scenario_file="steer-unsupported.json",
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            conversation = await create_host_conversation(client, scenario)

            async def queue_and_withdraw() -> None:
                await _append(client, conversation, "Never mind this.")
                [message] = [
                    item
                    for item in await _messages(client, conversation)
                    if item.text == "Never mind this."
                ]
                withdrawn = await client.delete(f"{conversation}/messages/{message.id}")
                assert withdrawn.status_code == 204, withdrawn.text
                traffic.with_suffix(".release").write_text("continue")

            _live_text, frames = await _turn(
                client,
                conversation,
                "Start the first task.",
                when_text_contains="Working without steering",
                meanwhile=queue_and_withdraw,
            )
            assert frames[-1].type == "completed", frames[-1]

            # A turn of the person's own is the barrier: had the withdrawn
            # message started a follow-up, that would be this conversation's
            # second prompt instead of this one.
            _live_text, frames = await _turn(
                client,
                conversation,
                "Next task.",
                when_text_contains="Working without steering",
                meanwhile=_nothing,
            )
            assert frames[-1].type == "completed", frames[-1]
            prompts = _sent(traffic, "session/prompt")
            assert len(prompts) == 2
            assert "Never mind this." not in str(prompts[1].message.params)
            assert "Next task." in str(prompts[1].message.params)
            assert not any(
                item.text == "Never mind this."
                for item in await _messages(client, conversation)
            )
