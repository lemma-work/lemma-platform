"""A run on the real Agent Host ends exactly once, whatever breaks under it.

Three failures, each struck while a scripted ACP agent is mid-answer:

* the backend dies -- its listener closes and every open connection is reset,
  so the host sees what a killed process looks like on the wire: no close
  frame, no ``reconnect``, then refused connections until it is back;
* the Agent Host dies -- SIGKILL, then ``serve`` again on the same data
  directory, which is what locald's supervisor does after a crash;
* the link is closed server-side, cleanly, while the agent is still talking.

What must hold every time is the delivery contract in
docs/architecture/agent-host.md "Delivery": the host keeps unacknowledged
events in its outbox and replays them, and Lemma de-duplicates by sequence. So
every event batch Lemma acknowledges is recorded as it is acknowledged, and the
test asserts the accepted sequences are contiguous from 1, that a replayed
sequence never carries a different event, and that there is exactly one
terminal event and it is the last. Then the durable outcome: one agent run, one
lease in one terminal state, the answer persisted once, and one
``session/prompt`` -- a recovery must never repeat a turn.

The backend is in process, so "dies" is its sockets dying, not its heap: the
API's own teardown of the dropped links still runs, which a SIGKILL would skip.
The worker is a separate process here as in production and is not touched.
Only the provider is scripted.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import socket
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, JsonValue, SecretStr
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.api.agent_host_schemas import (
    AgentHostHarnessListResponse,
    AgentHostListResponse,
)
from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostEventAck,
    AgentHostEventBatch,
    AgentHostEventType,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import AgentRunStatus, JsonObject
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.services.agent_host_link_registry import link_registry
from app.modules.agent.services.agent_host_link_session import AgentHostLinkSession
from app.modules.agent.services.agent_host_link_store import AgentHostLinkStore
from app.modules.agent.services.agent_host_link_wire import INTERNAL_ERROR_CLOSE
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.local_cli, pytest.mark.approval_worker]

_REPOSITORY = Path(__file__).resolve().parents[6]
_FIXTURES = _REPOSITORY / "desktop/agent-host/tests/fixtures"
_INITIAL_TEXT = "前 café 👩🏽‍💻\n"
_COMPLETE_TEXT = _INITIAL_TEXT + "second line\n完成"

Fault = Literal["backend-killed", "host-killed", "link-dropped"]


class ResourceId(BaseModel):
    id: UUID


class PairingCode(BaseModel):
    pairing_code: SecretStr


class SavedMessage(BaseModel):
    role: str
    kind: str
    text: str | None = None


class SavedMessages(BaseModel):
    items: list[SavedMessage]


class AcpMessage(BaseModel):
    method: str | None = None


class AcpRecord(BaseModel):
    direction: str
    message: AcpMessage


class StreamFrame(BaseModel):
    type: str
    kind: str | None = None
    data: JsonValue = None


@dataclass(frozen=True)
class AcceptedEvent:
    type: str
    object_id: str | None
    payload: JsonObject


@dataclass
class IntakeLedger:
    """Every event batch Lemma acknowledged, in the order it acknowledged them."""

    accepted: dict[UUID, dict[int, AcceptedEvent]] = field(default_factory=dict)
    watermark: dict[UUID, int] = field(default_factory=dict)
    replayed: int = 0

    def record(self, batch: AgentHostEventBatch, ack: AgentHostEventAck) -> None:
        run = self.accepted.setdefault(ack.run_id, {})
        for event in batch.events:
            seen = AcceptedEvent(event.type.value, event.object_id, event.payload)
            earlier = run.setdefault(event.sequence, seen)
            if earlier is not seen:
                self.replayed += 1
            assert earlier == seen, (
                f"sequence {event.sequence} was replayed as a different event: "
                f"{earlier} then {seen}"
            )
        self.watermark[ack.run_id] = max(
            self.watermark.get(ack.run_id, 0), ack.acked_through
        )

    def delivered_once(self, run_id: UUID) -> list[AcceptedEvent]:
        run = self.accepted.get(run_id)
        assert run, "Lemma never accepted an event for the run"
        sequences = sorted(run)
        last = self.watermark[run_id]
        assert sequences == list(range(1, last + 1)), (
            f"accepted sequences are not 1..{last} without a gap: {sequences}"
        )
        events = [run[sequence] for sequence in sequences]
        terminals = [
            index
            for index, event in enumerate(events)
            if event.type == AgentHostEventType.TERMINAL.value
        ]
        assert terminals == [len(events) - 1], (
            f"expected one terminal event, last; found {terminals} of {len(events)}"
        )
        return events


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class KillableBackend:
    """The real ASGI app on a real port, which can die and come back on it.

    ``crash`` closes the listener and resets every open connection; ``recover``
    listens again on the same port with the same application. The lifespan is
    deliberately not cycled: a crashed process never runs its shutdown, and the
    one that replaces it starts with the same configuration.
    """

    def __init__(self, app: FastAPI) -> None:
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = uvicorn.Server(
            uvicorn.Config(
                app=app,
                host="127.0.0.1",
                port=self.port,
                log_level="warning",
                access_log=False,
                lifespan="on",
                ws="websockets-sansio",
            )
        )
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        task = asyncio.create_task(self._server.serve())
        self._task = task

        async def started() -> bool:
            if task.done():
                raise RuntimeError("the backend exited before startup") from (
                    task.exception()
                )
            return self._server.started

        await eventually(
            label="the backend listening",
            probe=started,
            done=bool,
            timeout_seconds=10,
            interval_seconds=0.1,
        )

    def crash(self) -> None:
        for listener in self._server.servers:
            listener.close()
        for connection in list(self._server.server_state.connections):
            transport = getattr(connection, "transport", None)
            if transport is not None:
                transport.abort()

    async def recover(self) -> None:
        config = self._server.config
        protocol = partial(
            config.http_protocol_class,
            config=config,
            server_state=self._server.server_state,
            app_state=self._server.lifespan.state,
        )
        listener = await asyncio.get_running_loop().create_server(
            protocol, host=config.host, port=config.port, backlog=config.backlog
        )
        self._server.servers = [listener]

    async def stop(self) -> None:
        if self._task is None:
            return
        self._server.should_exit = True
        try:
            async with asyncio.timeout(10):
                await self._task
        except TimeoutError:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


@asynccontextmanager
async def killable_backend(app: FastAPI) -> AsyncIterator[KillableBackend]:
    backend = KillableBackend(app)
    await backend.start()
    try:
        yield backend
    finally:
        await backend.stop()


class ScriptedHost:
    """The real ``lemma-agent-host`` binary, with one scripted ACP agent on PATH."""

    def __init__(self, root: Path, base_url: str) -> None:
        self.root = root
        self.base_url = base_url
        self.traffic = root / "acp-stream.jsonl"
        self.release = self.traffic.with_suffix(".release")
        self._binary = Path(
            os.environ.get(
                "LEMMA_AGENT_HOST_E2E_BINARY",
                str(_REPOSITORY / "desktop/target/debug/lemma-agent-host"),
            )
        )
        assert self._binary.is_file(), (
            "Build lemma-agent-host first: make desktop-agent-host-e2e"
        )
        shims = root / "shim-bin"
        shims.mkdir()
        agent = shlex.join(
            [
                sys.executable,
                str(_FIXTURES / "scripted_acp_agent.py"),
                str(self.traffic),
                f"json:{_FIXTURES / 'scenarios/stream.json'}",
            ]
        )
        shim = shims / "cursor-agent"
        shim.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            "--version) echo '2026.7.31' ;;\n"
            f"*) exec {agent} ;;\n"
            "esac\n"
        )
        shim.chmod(0o700)
        self._environment = {
            **os.environ,
            "LEMMA_AGENT_HOST_PATH": str(shims),
            "LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD": "1",
            "RUST_LOG": "lemma_agent_host=info",
        }
        self._log = (root / "host.log").open("ab")
        self._process: asyncio.subprocess.Process | None = None

    async def _spawn(self, *arguments: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            str(self._binary),
            "--data-dir",
            str(self.root),
            *arguments,
            env=self._environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=self._log,
            stderr=self._log,
            # Its own group, so a kill takes the host and nothing else. The
            # adapter already runs in a group of its own, as it does under
            # locald, and is orphaned by the kill exactly as it would be there.
            start_new_session=True,
        )

    async def pair(self, code: SecretStr) -> None:
        pairing = await self._spawn(
            "connect",
            "--url",
            self.base_url,
            "--pairing-code",
            code.get_secret_value(),
            "--allow-insecure-http",
        )
        try:
            async with asyncio.timeout(30):
                assert await pairing.wait() == 0, "the Rust host could not pair"
        finally:
            if pairing.returncode is None:
                pairing.kill()
                await pairing.wait()

    async def serve(self) -> None:
        self._process = await self._spawn("serve")

    async def kill(self) -> None:
        assert self._process is not None
        with suppress(ProcessLookupError):
            os.killpg(self._process.pid, signal.SIGKILL)
        await self._process.wait()
        self._process = None

    async def close(self) -> None:
        # An adapter orphaned by a kill exits once released or once its stdin
        # closes; releasing it here bounds that either way.
        self.release.write_text("continue")
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                async with asyncio.timeout(10):
                    await self._process.wait()
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._log.close()

    def prompts(self) -> int:
        return sum(
            AcpRecord.model_validate_json(line).message.method == "session/prompt"
            for line in self.traffic.read_text().splitlines()
        )


async def _create(client: httpx.AsyncClient, path: str, body: JsonObject) -> UUID:
    response = await client.post(path, json=body)
    assert response.is_success, response.text
    return ResourceId.model_validate(response.json()).id


async def _only_host(client: httpx.AsyncClient) -> UUID:
    response = await client.get("/me/runtime/agent-hosts")
    assert response.is_success, response.text
    hosts = AgentHostListResponse.model_validate(response.json()).items
    assert len(hosts) == 1, "the test account should have exactly its isolated host"
    return hosts[0].id


async def _host_conversation(
    client: httpx.AsyncClient, scenario: E2EScenario, host_id: UUID
) -> tuple[UUID, str]:
    async def harnesses() -> AgentHostHarnessListResponse:
        response = await client.get(f"/me/runtime/agent-hosts/{host_id}/harnesses")
        assert response.is_success, response.text
        return AgentHostHarnessListResponse.model_validate(response.json())

    published = await eventually(
        label="scripted agent ready on the real Rust host",
        probe=harnesses,
        done=lambda listing: any(
            item.harness_key == "cursor" and item.health == "READY"
            for item in listing.items
        ),
        timeout_seconds=45,
    )
    harness = next(item for item in published.items if item.harness_key == "cursor")
    profile_id = await _create(
        client,
        f"/organizations/{scenario.org_id}/agent-runtime/profiles",
        {"source": "AGENT_HOST", "name": "Chaos agent", "harness_id": str(harness.id)},
    )
    agent_name = f"chaos_host_{uuid4().hex[:8]}"
    await _create(
        client,
        f"/pods/{scenario.pod_id}/agents",
        {
            "name": agent_name,
            "instruction": "Reply directly.",
            "toolsets": [],
            "agent_runtime": {"profile_id": str(profile_id)},
        },
    )
    conversation_id = await _create(
        client,
        f"/pods/{scenario.pod_id}/conversations",
        {"agent_name": agent_name, "title": "Chaos"},
    )
    return conversation_id, f"/pods/{scenario.pod_id}/conversations/{conversation_id}"


async def _answer_is_streaming(client: httpx.AsyncClient, path: str) -> None:
    """Send one turn and return once its first line is live, leaving it running.

    The SSE stream is closed on purpose: the run belongs to the worker and the
    host, and has to finish without anyone watching it.
    """
    text = ""
    async with asyncio.timeout(90):
        async with client.stream(
            "POST", f"{path}/messages", json={"content": "Keep going."}
        ) as response:
            assert response.is_success, await response.aread()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                frame = StreamFrame.model_validate_json(line.removeprefix("data: "))
                assert frame.type not in {"completed", "error", "stopped"}, frame
                if frame.type == "token" and frame.kind == "text":
                    assert isinstance(frame.data, str)
                    text += frame.data
                    if text == _INITIAL_TEXT:
                        return
    raise AssertionError(f"the stream ended before the first line; saw {text!r}")


async def _links_of(host_id: UUID) -> list[AgentHostLinkSession]:
    return [
        session
        for session in list(link_registry._sessions)
        if session.host_id == host_id
    ]


@dataclass(frozen=True)
class RunOutcome:
    run_id: UUID
    run_status: str
    lease_state: str


async def _finished_run(conversation_id: UUID) -> RunOutcome | None:
    async with async_session_maker() as session:
        runs = (
            await session.scalars(
                select(AgentRunModel).where(
                    AgentRunModel.conversation_id == conversation_id
                )
            )
        ).all()
        assert len(runs) <= 1, f"one turn became {len(runs)} runs"
        if not runs or runs[0].status == AgentRunStatus.RUNNING.value:
            return None
        leases = (
            await session.scalars(
                select(AgentHostRunLeaseModel).where(
                    AgentHostRunLeaseModel.run_id == runs[0].id
                )
            )
        ).all()
        assert len(leases) == 1, f"the run holds {len(leases)} leases"
        # The lease is the host's account of the run and arrives on its own
        # control frame, after the events that ended the conversation's turn.
        if AgentHostRunState(leases[0].state) not in TERMINAL_AGENT_HOST_RUN_STATES:
            return None
        return RunOutcome(runs[0].id, runs[0].status, leases[0].state)


class Strike:
    """A fault armed to fire inside Lemma's acceptance of an event batch.

    Armed once the first line is live, so it lands on the rest of the answer.
    It fires after the batch is durably appended and before its ``events_ok``
    is sent -- the one moment where losing the connection leaves the host
    holding events Lemma already has. Nothing else makes a replay certain: a
    fault between batches only delays the next one.
    """

    def __init__(self) -> None:
        self._fault: Callable[[], Awaitable[None]] | None = None
        self.fired = asyncio.Event()

    def arm(self, fault: Callable[[], Awaitable[None]]) -> None:
        self._fault = fault

    async def after_append(self) -> None:
        if self._fault is None:
            return
        fault, self._fault = self._fault, None
        # Set first: a fault that ends this connection may cancel the very
        # task running it.
        self.fired.set()
        await fault()


async def _strike(
    fault: Fault,
    strike: Strike,
    backend: KillableBackend,
    host: ScriptedHost,
    host_id: UUID,
) -> None:
    if fault == "host-killed":
        # While the agent is still waiting to finish: the turn is with the
        # provider, and only the host knows it.
        await host.kill()
        await host.serve()
        return

    async def kill_backend() -> None:
        backend.crash()

        async def come_back() -> None:
            await asyncio.sleep(2)
            await backend.recover()

        restarts.add(asyncio.create_task(come_back()))

    async def drop_link() -> None:
        # Close, then wait for the close: left to itself the session would
        # finish answering this batch first, and a clean close that always
        # lets the answer out proves nothing about replay. Waiting here is
        # cut short by the close cancelling this handler, which is the point.
        links = await _links_of(host_id)
        for link in links:
            link.stop(INTERNAL_ERROR_CLOSE, "chaos")
        for link in links:
            await link.finished()

    restarts: set[asyncio.Task[None]] = set()
    strike.arm(kill_backend if fault == "backend-killed" else drop_link)
    host.release.write_text("continue")
    async with asyncio.timeout(30):
        await strike.fired.wait()
    await asyncio.gather(*restarts)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["backend-killed", "host-killed", "link-dropped"])
async def test_a_run_finishes_exactly_once_whatever_breaks_mid_stream(
    scenario: E2EScenario,
    test_app: FastAPI,
    e2e_process_clients: None,
    worker: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: Fault,
) -> None:
    del worker, e2e_process_clients
    ledger = IntakeLedger()
    strike = Strike()
    accept = AgentHostLinkStore.append_events

    async def recording_accept(
        store: AgentHostLinkStore, *, host_id: UUID, batch: AgentHostEventBatch
    ) -> AgentHostEventAck:
        ack = await accept(store, host_id=host_id, batch=batch)
        ledger.record(batch, ack)
        await strike.after_append()
        return ack

    monkeypatch.setattr(AgentHostLinkStore, "append_events", recording_accept)

    await scenario.create_org_with_pod(name_prefix=f"Chaos {fault}")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings", json={"display_name": f"chaos {fault}"}
    )
    assert minted.is_success, minted.text
    code = PairingCode.model_validate(minted.json()).pairing_code

    async with killable_backend(test_app) as backend:
        host = ScriptedHost(tmp_path, backend.base_url)
        try:
            await host.pair(code)
            await host.serve()
            async with httpx.AsyncClient(
                base_url=backend.base_url,
                headers=scenario.owner_client.headers,
                timeout=90,
            ) as client:
                host_id = await _only_host(client)
                conversation_id, path = await _host_conversation(
                    client, scenario, host_id
                )
                await _answer_is_streaming(client, path)
                await _strike(fault, strike, backend, host, host_id)

                finished = await eventually(
                    label=f"the run ended after {fault}",
                    probe=lambda: _finished_run(conversation_id),
                    done=lambda value: value is not None,
                    timeout_seconds=90,
                )
                assert finished is not None
                events = ledger.delivered_once(finished.run_id)

                if fault == "host-killed":
                    # The turn had reached the provider, so the restarted host
                    # must not send it again; it ends the run and says why.
                    expected_text = _INITIAL_TEXT
                    assert finished.lease_state == AgentHostRunState.DISPATCH_UNKNOWN
                    assert finished.run_status == AgentRunStatus.FAILED
                else:
                    expected_text = _COMPLETE_TEXT
                    assert finished.lease_state == AgentHostRunState.SUCCEEDED
                    assert finished.run_status == AgentRunStatus.COMPLETED
                assert (
                    AgentHostRunState(finished.lease_state)
                    in TERMINAL_AGENT_HOST_RUN_STATES
                )
                streamed = "".join(
                    str(event.payload.get("text", ""))
                    for event in events
                    if event.type == AgentHostEventType.AGENT_MESSAGE_CHUNK.value
                )
                assert streamed == expected_text

                async def answers() -> list[str | None]:
                    response = await client.get(f"{path}/messages")
                    assert response.is_success, response.text
                    return [
                        item.text
                        for item in SavedMessages.model_validate(response.json()).items
                        if item.role == "assistant" and item.kind == "TEXT"
                    ]

                saved = await eventually(
                    label="the answer persisted once",
                    probe=answers,
                    done=lambda texts: texts == [expected_text],
                    timeout_seconds=30,
                )
                assert saved == [expected_text]
                assert host.prompts() == 1, "a recovery repeated provider dispatch"
                if fault != "host-killed":
                    assert ledger.replayed, (
                        "the host replayed nothing, so the fault struck no batch in flight"
                    )
        finally:
            await host.close()
