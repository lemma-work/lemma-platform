"""A real call: a real voice, a real coordinator and real sub-agents.

The person types their turns (the socket's `text` frame) so the run does not
depend on a microphone; everything after that is what a spoken call does. The
voice is Gemini Live, the agents run on the deployment's real model in the real
worker, and the test holds the call to what makes it useful:

- the request reaches the coordinator, which hands it to a sub-agent rather
  than doing it in the call's own turn;
- a correction mid-work reaches that sub-agent;
- something is put on the canvas, and the voice is never left silent.

It writes the whole call -- frames, transcript, the conversation tree -- to
``CALL_E2E_RECORD`` when set, because what the agents chose to say and show is
the part a reviewer reads, not the part an assertion can.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from app.modules.test_support.e2e.waiters import eventually
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.provider,
    pytest.mark.real_llm,
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
    pytest.mark.skipif(
        not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY is not set."
    ),
    pytest.mark.timeout(600),
]

_INSTRUCTION = (
    "You are Ria, a research teammate. You look things up on the web and turn "
    "them into clear comparisons."
)


async def _ws(base_url: str, pod_id: str, call_id: str, token: str):
    import websockets

    url = (
        base_url.replace("http://", "ws://") + f"/pods/{pod_id}/calls/{call_id}/socket"
    )
    return await websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {token}"},
        max_size=8 * 1024 * 1024,
    )


class Call:
    def __init__(self, socket) -> None:
        self.socket = socket
        self.frames: list[dict[str, object]] = []
        self.audio_bytes = 0
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for message in self.socket:
            if isinstance(message, bytes):
                self.audio_bytes += len(message)
                continue
            frame = json.loads(message)
            frame["at"] = round(time.monotonic(), 2)
            self.frames.append(frame)

    async def say(self, text: str) -> None:
        await self.socket.send(json.dumps({"type": "text", "text": text}))

    def of(self, kind: str) -> list[dict[str, object]]:
        return [frame for frame in self.frames if frame["type"] == kind]

    def voice_lines(self) -> list[str]:
        return [
            str(f["text"])
            for f in self.of("transcript")
            if f["speaker"] == "voice" and f["final"]
        ]

    async def until(self, label: str, check, *, seconds: float) -> None:
        await eventually(
            label=label,
            probe=self._now,
            done=lambda _: bool(check()),
            timeout_seconds=seconds,
            interval_seconds=0.5,
        )

    async def _now(self) -> int:
        return len(self.frames)

    async def close(self) -> None:
        await self.socket.send(json.dumps({"type": "end"}))
        await self.socket.close()
        self._reader.cancel()


async def _running_child(client, pod_id: str, call_id: str) -> str | None:
    """The coordinator's sub-agent, while its run is still going."""
    page = (
        await client.get(
            f"/pods/{pod_id}/conversations/{call_id}/messages", params={"limit": 200}
        )
    ).json()
    for message in page.get("items", []):
        result = message.get("tool_result")
        if (
            message.get("tool_name") == "spawn_subagent"
            and message.get("kind") == "TOOL_RETURN"
            and isinstance(result, dict)
            and result.get("conversation_id")
        ):
            child = (
                await client.get(
                    f"/pods/{pod_id}/conversations/{result['conversation_id']}"
                )
            ).json()
            if (
                child.get("status") == "RUNNING"
                or child.get("last_run_status") == "RUNNING"
            ):
                return result["conversation_id"]
    return None


async def _record(client, pod_id: str, call_id: str, call: Call, started: float):
    """The call's conversations, walked from the coordinator through each spawn,
    and -- when ``CALL_E2E_RECORD`` names a file -- everything written there."""
    tree = {}
    pending = [(call_id, None)]
    while pending:
        conversation_id, parent_id = pending.pop()
        if conversation_id in tree:
            continue
        messages = (
            await client.get(
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                params={"limit": 200},
            )
        ).json()
        # A sub-agent's conversation is named by the spawn that made it;
        # the pod's conversation list leaves sub-agents out.
        pending += [
            (m["tool_result"]["conversation_id"], conversation_id)
            for m in messages.get("items", [])
            if m.get("tool_name") == "spawn_subagent"
            and m.get("kind") == "TOOL_RETURN"
            and isinstance(m.get("tool_result"), dict)
            and m["tool_result"].get("conversation_id")
        ]
        tree[conversation_id] = {
            "parent_id": parent_id,
            "messages": [
                {
                    key: m.get(key)
                    for key in (
                        "role",
                        "kind",
                        "text",
                        "tool_name",
                        "tool_args",
                        "tool_result",
                    )
                }
                for m in messages.get("items", [])
            ],
        }
    record_to = os.getenv("CALL_E2E_RECORD")
    if record_to:
        Path(record_to).write_text(
            json.dumps(
                {
                    "model": os.getenv("LEMMA_OPENAI_DEFAULT_MODEL"),
                    "seconds": round(time.monotonic() - started, 1),
                    "audio_seconds": round(call.audio_bytes / 48_000, 1),
                    "frames": call.frames,
                    "tree": tree,
                },
                indent=1,
                default=str,
            )
        )

    return tree


async def test_a_call_delegates_steers_and_shows_its_work(
    scenario, worker, backend_server
) -> None:
    client = scenario.owner_client
    await scenario.create_org_with_pod(name_prefix="call")
    pod_id = scenario.pod_id
    created = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "ria",
            "instruction": _INSTRUCTION,
            "agent_runtime": {"profile_id": "system:lemma"},
            "toolsets": ["WEB_SEARCH", "SUBAGENTS", "WAIT"],
        },
    )
    assert created.status_code == 201, created.text
    config = await client.get(f"/pods/{pod_id}/calls/config")
    assert "gemini_live" in config.json()["engines"]
    opened = await client.post(
        f"/pods/{pod_id}/calls", json={"agent_name": "ria", "display_name": "Ria"}
    )
    assert opened.status_code == 201, opened.text
    call_id = opened.json()["call_id"]

    socket = await _ws(
        backend_server["host_base_url"], pod_id, call_id, scenario.owner_user["token"]
    )
    await socket.send(
        json.dumps(
            {"type": "start", "teammate": "Ria", "pod": "Research", "context": ""}
        )
    )
    call = Call(socket)
    started = time.monotonic()
    try:
        await call.until("the voice is ready", lambda: call.of("ready"), seconds=20)
        await call.say(
            "I'm planning a team offsite in Goa for 12 people in December. Can you find "
            "three good venues with their nightly prices, and show me a comparison?"
        )
        await call.until("the voice answers", call.voice_lines, seconds=30)
        # The correction has to land on work in progress, or it proves nothing
        # about steering: wait for the coordinator's sub-agent to be running.
        await eventually(
            label="a sub-agent is working on the request",
            probe=lambda: _running_child(client, pod_id, call_id),
            done=bool,
            timeout_seconds=120,
            interval_seconds=1.0,
        )
        await call.say("Oh, and actually make it 15 people, not 12.")
        await call.until(
            "something is on the canvas", lambda: call.of("canvas.item"), seconds=300
        )
        said_before = len(call.voice_lines())
        await call.until(
            "the voice says what was found",
            lambda: len(call.voice_lines()) > said_before,
            seconds=60,
        )
    finally:
        await call.close()
        tree = await _record(client, pod_id, call_id, call, started)

    coordinator = tree[call_id]["messages"]
    tools = [m["tool_name"] for m in coordinator if m["kind"] == "TOOL_CALL"]
    assert "spawn_subagent" in tools, tools
    assert "relay_to_voice" in tools, tools
    assert any(
        m["kind"] == "TEXT" and m["role"] == "user" and "15" in (m["text"] or "")
        for m in coordinator
    )
    assert len(tree) >= 2, "no sub-agent conversation"
    assert call.audio_bytes > 0
