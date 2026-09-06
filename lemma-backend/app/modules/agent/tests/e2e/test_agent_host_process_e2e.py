"""A Rust Agent Host and ACP subprocess deliver a real conversation SSE stream.

Only the provider is scripted. Pairing, profile creation, queued dispatch,
HTTP transport, Redis intake, normalization, and message persistence are real.
The provider waits for the HTTP client to observe text before it can finish.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel, Field, JsonValue, SecretStr

from app.modules.agent.api.agent_host_schemas import (
    AgentHostHarnessListResponse,
    AgentHostHarnessResponse,
    AgentHostListResponse,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.local_cli, pytest.mark.approval_worker]

_REPOSITORY = Path(__file__).resolve().parents[6]
_INITIAL_TEXT = "前 café 👩🏽‍💻\n"
_COMPLETE_TEXT = _INITIAL_TEXT + "second line\n完成"


class ResourceId(BaseModel):
    id: UUID


class PairingCode(BaseModel):
    pairing_code: SecretStr


class SavedMessage(BaseModel):
    id: UUID
    role: str
    kind: str
    text: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: JsonObject | None = None
    tool_result: JsonValue = None


class SavedMessages(BaseModel):
    items: list[SavedMessage]


class AcpMessage(BaseModel):
    id: int | str | None = None
    method: str | None = None
    params: JsonObject = Field(default_factory=dict)
    result: JsonValue = None


class AcpRecord(BaseModel):
    direction: str
    message: AcpMessage


class StreamFrame(BaseModel):
    type: str
    kind: str | None = None
    data: JsonValue = None


class BrowserRunConfig(BaseModel):
    apiUrl: str
    conversationUrl: str
    token: str = Field(repr=False)
    action: str
    artifactDirectory: str
    releaseFile: str


async def create_resource(
    client: httpx.AsyncClient, path: str, body: JsonObject
) -> UUID:
    response = await client.post(path, json=body)
    assert response.is_success, response.text
    return ResourceId.model_validate(response.json()).id


@asynccontextmanager
async def running_host(
    root: Path, base_url: str, pairing_code: SecretStr, *, scenario_file: str
) -> AsyncIterator[Path]:
    assert os.name == "posix", "The scripted ACP process lane runs on macOS or Linux"
    binary = Path(
        os.environ.get(
            "LEMMA_AGENT_HOST_E2E_BINARY",
            str(_REPOSITORY / "desktop/target/debug/lemma-agent-host"),
        )
    )
    assert binary.is_file(), "Build lemma-agent-host first: make desktop-agent-host-e2e"
    fixture = _REPOSITORY / "desktop/agent-host/tests/fixtures/scripted_acp_agent.py"
    shims = root / "shim-bin"
    shims.mkdir()
    traffic = root / "acp-stream.jsonl"
    scenario_path = fixture.parent / "scenarios" / scenario_file
    command = shlex.join(
        [
            sys.executable,
            str(fixture),
            str(traffic),
            f"json:{scenario_path}",
        ]
    )
    script = (
        "#!/bin/sh\n"
        'case "$1" in\n'
        "--version) echo '2026.7.31' ;;\n"
        f"*) exec {command} ;;\n"
        "esac\n"
    )
    for name in ("cursor-agent", "opencode"):
        shim = shims / name
        shim.write_text(script)
        shim.chmod(0o700)
    environment = {
        **os.environ,
        "LEMMA_AGENT_HOST_PATH": str(shims),
        "LEMMA_AGENT_HOST_SKIP_ADAPTER_DOWNLOAD": "1",
        "RUST_LOG": "lemma_agent_host=info",
    }
    with (root / "host.log").open("wb") as log:
        pairing = await asyncio.create_subprocess_exec(
            str(binary),
            "--data-dir",
            str(root),
            "connect",
            "--url",
            base_url,
            "--pairing-code",
            pairing_code.get_secret_value(),
            "--allow-insecure-http",
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            async with asyncio.timeout(30):
                assert await pairing.wait() == 0, "the Rust host could not pair"
        finally:
            if pairing.returncode is None:
                pairing.kill()
                await pairing.wait()
        process = await asyncio.create_subprocess_exec(
            str(binary),
            "--data-dir",
            str(root),
            "serve",
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            yield traffic
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    async with asyncio.timeout(10):
                        await process.wait()
                except TimeoutError:
                    process.kill()
                    await process.wait()


async def ready_harness(client: httpx.AsyncClient) -> AgentHostHarnessResponse:
    response = await client.get("/me/runtime/agent-hosts")
    assert response.is_success, response.text
    hosts = AgentHostListResponse.model_validate(response.json()).items
    assert len(hosts) == 1, "the test account should have exactly its isolated host"
    host_id = hosts[0].id

    async def published() -> list[AgentHostHarnessResponse]:
        response = await client.get(f"/me/runtime/agent-hosts/{host_id}/harnesses")
        assert response.is_success, response.text
        return AgentHostHarnessListResponse.model_validate(response.json()).items

    harnesses = await eventually(
        label="scripted agent ready on the real Rust host",
        probe=published,
        done=lambda items: any(
            item.harness_key == "cursor" and item.health == "READY" for item in items
        ),
        timeout_seconds=45,
    )
    return next(item for item in harnesses if item.harness_key == "cursor")


async def create_host_conversation(
    client: httpx.AsyncClient, scenario: E2EScenario
) -> str:
    harness = await ready_harness(client)
    profile_id = await create_resource(
        client,
        f"/organizations/{scenario.org_id}/agent-runtime/profiles",
        {
            "source": "AGENT_HOST",
            "name": "Scripted local agent",
            "harness_id": str(harness.id),
        },
    )
    agent_name = f"streaming_host_{uuid4().hex[:8]}"
    await create_resource(
        client,
        f"/pods/{scenario.pod_id}/agents",
        {
            "name": agent_name,
            "instruction": "Reply directly.",
            "toolsets": [],
            "agent_runtime": {"profile_id": str(profile_id)},
        },
    )
    conversation_id = await create_resource(
        client,
        f"/pods/{scenario.pod_id}/conversations",
        {"agent_name": agent_name, "title": "Full host streaming"},
    )
    return f"/pods/{scenario.pod_id}/conversations/{conversation_id}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["two-turns", "client-disconnect", "provider-crash"]
)
async def test_rust_host_streams_into_the_real_conversation_and_persists_its_answer(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
    outcome: Literal["two-turns", "client-disconnect", "provider-crash"],
) -> None:
    del worker
    disconnect = outcome == "client-disconnect"
    crash = outcome == "provider-crash"
    await scenario.create_org_with_pod(name_prefix="Host process stream")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "isolated streaming host"},
    )
    assert minted.is_success, minted.text
    pairing = PairingCode.model_validate(minted.json())
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path,
        base_url,
        pairing.pairing_code,
        scenario_file="crash.json" if crash else "stream.json",
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            conversation_path = await create_host_conversation(client, scenario)
            path = f"{conversation_path}/messages"
            turn_count = 2 if outcome == "two-turns" else 1
            expected_text = _INITIAL_TEXT if crash else _COMPLETE_TEXT
            for turn in range(turn_count):
                release = traffic.with_suffix(".release")
                release.unlink(missing_ok=True)
                frames: list[StreamFrame] = []
                live_text = ""
                received_live = False
                async with asyncio.timeout(90):
                    async with client.stream(
                        "POST", path, json={"content": f"Stream turn {turn + 1}."}
                    ) as response:
                        assert response.is_success, await response.aread()
                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            frame = StreamFrame.model_validate_json(
                                line.removeprefix("data: ")
                            )
                            frames.append(frame)
                            if frame.type == "token" and frame.kind == "text":
                                assert isinstance(frame.data, str)
                                live_text += frame.data
                                if not received_live and live_text == _INITIAL_TEXT:
                                    assert not any(
                                        item.type in {"completed", "error", "stopped"}
                                        for item in frames
                                    )
                                    received_live = True
                                    if disconnect:
                                        break
                                    release.write_text("continue")
                            if frame.type in {"completed", "error", "stopped"}:
                                break
                assert received_live, (
                    "the HTTP client never received text while the ACP agent was still running"
                )
                if disconnect:
                    # Release only after closing the HTTP stream. The worker and
                    # host must complete independently of a browser connection.
                    release.write_text("continue")
                else:
                    assert frames[-1].type == ("error" if crash else "completed"), (
                        frames[-1]
                    )
                    assert live_text == expected_text

                async def saved_answers() -> list[SavedMessage]:
                    response = await client.get(path)
                    assert response.is_success, response.text
                    messages = SavedMessages.model_validate(response.json()).items
                    return [
                        item
                        for item in messages
                        if item.role == "assistant" and item.kind == "TEXT"
                    ]

                saved = await eventually(
                    label="complete answer persisted after HTTP stream closure",
                    probe=saved_answers,
                    done=lambda messages: (
                        [item.text for item in messages] == [expected_text] * (turn + 1)
                    ),
                    timeout_seconds=30,
                )
                assert [item.text for item in saved] == [expected_text] * (turn + 1)

            records = [
                AcpRecord.model_validate_json(line)
                for line in traffic.read_text().splitlines()
            ]
            prompts = [
                record.message
                for record in records
                if record.direction == "client->agent"
                and record.message.method == "session/prompt"
            ]
            assert len(prompts) == turn_count, (
                "a reconnect or follow-up repeated provider dispatch"
            )
            if turn_count == 2:
                assert "Stream turn 1." in str(prompts[1].params)
                assert "Stream turn 2." in str(prompts[1].params)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "deny", "cancel", "parallel"])
async def test_json_acp_tools_obey_the_public_conversation_decision(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
    action: Literal["approve", "deny", "cancel", "parallel"],
) -> None:
    del worker
    await scenario.create_org_with_pod(name_prefix="Host JSON actions")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "isolated JSON scenario host"},
    )
    assert minted.is_success, minted.text
    pairing = PairingCode.model_validate(minted.json())
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path,
        base_url,
        pairing.pairing_code,
        scenario_file={
            "cancel": "cancel.json",
            "parallel": "parallel-approvals.json",
        }.get(action, "tool-approval.json"),
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            path = await create_host_conversation(client, scenario)
            frames: list[StreamFrame] = []
            live_text = ""
            answered = False
            pending_approvals: list[str] = []
            async with asyncio.timeout(90):
                async with client.stream(
                    "POST", f"{path}/messages", json={"content": "Run the test action."}
                ) as response:
                    assert response.is_success, await response.aread()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        frame = StreamFrame.model_validate_json(
                            line.removeprefix("data: ")
                        )
                        frames.append(frame)
                        if frame.type == "token" and frame.kind == "text":
                            assert isinstance(frame.data, str)
                            live_text += frame.data
                            if action == "cancel" and not answered:
                                stopped = await client.post(f"{path}/stop")
                                assert stopped.status_code == 200, stopped.text
                                traffic.with_suffix(".release").write_text(
                                    "Stop acknowledged."
                                )
                                answered = True
                        if frame.type == "message":
                            message = SavedMessage.model_validate(frame.data)
                            if (
                                message.kind == "TOOL_CALL"
                                and message.tool_name == "request_approval"
                            ):
                                assert not answered, "approval was delivered twice"
                                assert message.tool_call_id is not None
                                assert message.tool_call_id not in pending_approvals
                                pending_approvals.append(message.tool_call_id)
                                # The provider waits for this decision; there must
                                # be no tool result before the user grants access.
                                assert "Read approved" not in live_text
                                if action == "parallel" and len(pending_approvals) < 2:
                                    continue
                                for approval in reversed(pending_approvals):
                                    allow = action == "approve" or (
                                        action == "parallel"
                                        and approval.endswith("read-a")
                                    )
                                    decision = await client.post(
                                        f"{path}/approvals/{approval}/decision",
                                        json={
                                            "decision": "APPROVE_ONCE"
                                            if allow
                                            else "DENY"
                                        },
                                    )
                                    assert decision.status_code == 200, decision.text
                                answered = True
                        if frame.type in {"completed", "error", "stopped"}:
                            break
            assert answered, "the public stream never offered the expected action"
            assert frames[-1].type == "completed", frames[-1]
            terminal = frames[-1].data
            assert isinstance(terminal, dict)
            assert terminal["status"] == (
                "STOPPED" if action == "cancel" else "COMPLETED"
            )
            response = await client.get(f"{path}/messages")
            assert response.status_code == 200, response.text
            messages = SavedMessages.model_validate(response.json()).items
            texts = [
                message.text or ""
                for message in messages
                if message.role == "assistant" and message.kind == "TEXT"
            ]
            expected_answer = {
                "approve": "Read approved: # Mock project",
                "deny": "Read denied; no file was accessed.",
                "cancel": "Stopped as requested.",
                "parallel": "Read approved: # File A",
            }[action]
            assert expected_answer in live_text
            assert expected_answer in "".join(texts)
            if action != "cancel":
                calls = [m for m in messages if m.kind == "TOOL_CALL"]
                returns = [m for m in messages if m.kind == "TOOL_RETURN"]
                expected_ids = (
                    ["read-a", "read-b"] if action == "parallel" else ["read-project"]
                )
                assert sorted(m.tool_call_id for m in calls) == sorted(
                    expected_ids
                    + [f"agent-host-permission:{key}" for key in expected_ids]
                )
                assert sorted(m.tool_call_id for m in returns) == sorted(
                    m.tool_call_id for m in calls
                ), "every tool and approval must have one matching result"
                for tool_id in expected_ids:
                    native_call = next(m for m in calls if m.tool_call_id == tool_id)
                    assert native_call.tool_args == {
                        "path": f"{tool_id.removeprefix('read-')}.md"
                        if action == "parallel"
                        else "README.md"
                    }
                    native_result = next(
                        m.tool_result for m in returns if m.tool_call_id == tool_id
                    )
                    if action == "approve":
                        assert native_result == {"text": "# Mock project"}
                    elif action == "parallel" and tool_id == "read-a":
                        assert native_result == {"text": "# File A"}
                    else:
                        assert isinstance(native_result, dict)
                        assert native_result["success"] is False
            records = [
                AcpRecord.model_validate_json(line)
                for line in traffic.read_text().splitlines()
            ]
            assert sum(r.message.method == "session/prompt" for r in records) == 1
            if action == "cancel":
                assert sum(r.message.method == "session/cancel" for r in records) == 1
            elif action != "parallel":
                replies = [
                    r.message.result
                    for r in records
                    if r.direction == "client->agent" and r.message.id == 900
                ]
                assert replies == [
                    {
                        "outcome": {"outcome": "selected", "optionId": "once"}
                        if action == "approve"
                        else {"outcome": "cancelled"}
                    }
                ]
            else:
                replies_by_id = {
                    r.message.id: r.message.result
                    for r in records
                    if r.direction == "client->agent" and r.message.id in {901, 902}
                }
                assert replies_by_id == {
                    901: {"outcome": {"outcome": "selected", "optionId": "once"}},
                    902: {"outcome": {"outcome": "cancelled"}},
                }
                assert "Read denied; no file was accessed." in live_text


@pytest.mark.asyncio
@pytest.mark.agent_host_browser
@pytest.mark.parametrize(
    "action", ["approve", "deny", "stream", "crash", "disconnect", "cancel", "parallel"]
)
async def test_browser_chat_replays_json_acp_and_retains_results_after_reload(
    scenario: E2EScenario,
    backend_server: dict[str, str],
    worker: object,
    tmp_path: Path,
    action: Literal[
        "approve", "deny", "stream", "crash", "disconnect", "cancel", "parallel"
    ],
) -> None:
    del worker
    await scenario.create_org_with_pod(name_prefix="Browser ACP")
    minted = await scenario.owner_client.post(
        "/me/runtime/agent-host-pairings",
        json={"display_name": "isolated browser host"},
    )
    assert minted.is_success, minted.text
    pairing = PairingCode.model_validate(minted.json())
    base_url = backend_server["host_base_url"]
    async with running_host(
        tmp_path,
        base_url,
        pairing.pairing_code,
        scenario_file={
            "stream": "stream.json",
            "disconnect": "stream.json",
            "crash": "crash.json",
            "cancel": "cancel.json",
            "parallel": "parallel-approvals.json",
        }.get(action, "tool-approval.json"),
    ) as traffic:
        async with httpx.AsyncClient(
            base_url=base_url, headers=scenario.owner_client.headers, timeout=90
        ) as client:
            path = await create_host_conversation(client, scenario)
            conversation_id = path.rsplit("/", 1)[-1]
            artifacts = _REPOSITORY / "output/playwright" / f"agent-host-{uuid4().hex}"
            config = BrowserRunConfig(
                apiUrl=base_url,
                conversationUrl=f"/pod/{scenario.pod_id}/conversations/{conversation_id}",
                token=scenario.owner_client.headers["Authorization"].removeprefix(
                    "Bearer "
                ),
                action=action,
                artifactDirectory=str(artifacts),
                releaseFile=str(traffic.with_suffix(".release")),
            )
            driver = _REPOSITORY / "desktop/ui-tests/drivers/agent-host-chat.mjs"
            with (tmp_path / "browser.log").open("wb") as log:
                process = await asyncio.create_subprocess_exec(
                    "node",
                    str(driver),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    async with asyncio.timeout(180):
                        await process.communicate(config.model_dump_json().encode())
                finally:
                    if process.returncode is None:
                        # Playwright handles SIGTERM by closing its detached
                        # browser group; give that handler a bounded chance
                        # before killing the driver and its frontend server.
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            async with asyncio.timeout(5):
                                await process.wait()
                        except TimeoutError:
                            pass
                    # Reap the driver/frontend group even if the driver's own
                    # cleanup did not complete.
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()
            response = await client.get(f"{path}/messages")
            assert response.status_code == 200, response.text
            messages = SavedMessages.model_validate(response.json()).items
            conversation = await client.get(path)
            assert process.returncode == 0, (
                f"Browser journey failed; see {tmp_path / 'browser.log'} and {artifacts}; "
                f"status={conversation.json().get('status')}; "
                f"saved={[(m.kind, m.text) for m in messages]}"
            )
            if action in {"approve", "deny", "parallel"}:
                for tool_id in (
                    ["read-a", "read-b"] if action == "parallel" else ["read-project"]
                ):
                    assert sum(m.tool_call_id == tool_id for m in messages) == 2
            else:
                assert [
                    m.text
                    for m in messages
                    if m.role == "assistant" and m.kind == "TEXT"
                ] == [
                    {
                        "crash": _INITIAL_TEXT,
                        "cancel": "Started the requested work.\nStopped as requested.",
                    }.get(action, _COMPLETE_TEXT)
                ]
            records = [
                AcpRecord.model_validate_json(line)
                for line in traffic.read_text().splitlines()
            ]
            assert sum(r.message.method == "session/prompt" for r in records) == 1
            if action == "cancel":
                assert sum(r.message.method == "session/cancel" for r in records) == 1
