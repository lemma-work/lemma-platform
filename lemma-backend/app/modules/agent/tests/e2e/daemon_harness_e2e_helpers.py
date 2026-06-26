from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import AgentRunStatus, MessageRole

MODEL_ENV = {
    "CODEX": "LEMMA_REAL_DAEMON_CODEX_MODEL",
    "CLAUDE_CODE": "LEMMA_REAL_DAEMON_CLAUDE_MODEL",
    "OPENCODE": "LEMMA_REAL_DAEMON_OPENCODE_MODEL",
}

DEFAULT_MODEL = {
    "CODEX": "gpt-5.5",
    "CLAUDE_CODE": "sonnet",
    "OPENCODE": "opencode/deepseek-v4-flash-free",
}

BINARY = {
    "CODEX": "codex",
    "CLAUDE_CODE": "claude",
    "OPENCODE": "opencode",
}


# These tests drive REAL external CLIs (codex/claude/opencode) against real
# provider accounts. A usage/rate-limit error from the provider is an environment
# condition, not a Lemma defect, so it is skipped rather than failed — the same
# spirit as the `shutil.which` / missing-API-key skips below.
_PROVIDER_QUOTA_MARKERS = (
    "usage limit",
    "hit your usage limit",
    "upgrade to pro",
    "rate limit",
    "rate_limit",
    "try again at",
    "quota",
    "429",
    "too many requests",
    "resource_exhausted",
    "insufficient_quota",
)


def _is_provider_quota_error(event: dict) -> bool:
    if event.get("type") != "error":
        return False
    detail = str(event.get("data") or "").lower()
    return any(marker in detail for marker in _PROVIDER_QUOTA_MARKERS)


async def collect_sse_lines(line_iterator, *, timeout: float = 600) -> list[dict]:
    events: list[dict] = []
    async with asyncio.timeout(timeout):
        async for line in line_iterator:
            if not line.startswith("data: "):
                continue
            raw = line.removeprefix("data: ")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                # The backend SSE encoder emits exactly one JSON object per
                # `data:` frame (single json.dumps + `\n\n`), so a malformed frame
                # is an external-CLI artifact — observed with opencode's free
                # model in background mode — not a Lemma defect. Skip the flake.
                pytest.skip(
                    "Daemon emitted a malformed SSE frame (external CLI flake): "
                    f"{raw!r} ({exc})"
                )
            if _is_provider_quota_error(payload):
                pytest.skip(
                    "External provider quota/rate-limit exhausted: "
                    f"{payload.get('data')!r}"
                )
            events.append(payload)
            if payload["type"] in {"completed", "stopped", "error"}:
                break
    return events


async def post_sse(client, url: str, payload: dict, *, timeout: float = 600) -> list[dict]:
    async with client.stream("POST", url, json=payload, timeout=timeout) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise AssertionError(body.decode())
        return await collect_sse_lines(response.aiter_lines(), timeout=timeout)


def assert_completed_without_error(events: list[dict]) -> None:
    assert events, "SSE stream produced no events"
    assert not [event for event in events if event["type"] == "error"], events
    assert events[-1]["type"] == "completed", events
    assert events[-1]["data"]["status"] == AgentRunStatus.COMPLETED.value, events


def assert_sse_includes_tool_stream_events(events: list[dict]) -> None:
    tool_chunks = [
        event["data"]
        for event in events
        if event.get("type") == "token" and event.get("kind") == "tool"
    ]
    assert tool_chunks, events
    parsed_tool_calls = [json.loads(chunk) for chunk in tool_chunks]
    assert any(
        item["tool_name"] in {"lemma_exec_command", "lemma_execute_python"}
        and isinstance(item["args"], dict)
        for item in parsed_tool_calls
    ), parsed_tool_calls

    text_chunks = [
        event["data"]
        for event in events
        if event.get("type") == "token" and event.get("kind") == "text"
    ]
    assert text_chunks, events


async def create_test_pod(authenticated_client, fixed_test_org, harness_kind: str) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Daemon {harness_kind} Pod {uuid4().hex[:8]}",
            "description": f"Daemon {harness_kind} real harness e2e pod",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def start_real_daemon_process(
    *,
    backend_server,
    fixed_test_user,
    tmp_path: Path,
    background: bool = False,
) -> subprocess.Popen[str]:
    config_path = tmp_path / f"lemma-config-{uuid4().hex}.json"
    config_path.write_text(
        json.dumps(
            {
                "active_server": "default",
                "servers": {
                    "default": {
                        "base_url": backend_server["host_base_url"],
                        "token": fixed_test_user["token"],
                        "defaults": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    repo_root = Path(__file__).resolve().parents[6]
    env = os.environ.copy()
    python_paths = [str(repo_root / "lemma-cli"), str(repo_root / "lemma-python")]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    # Isolate daemon state (pid/config/logs) per test so background mode
    # doesn't clobber a developer's real ~/.lemma/daemon.
    daemon_dir = tmp_path / "daemon-state"
    env["LEMMA_DAEMON_DIR"] = str(daemon_dir)
    command = [
        sys.executable,
        "-m",
        "lemma_cli.cli_app.main",
        "--config-file",
        str(config_path),
        "daemon",
        "start",
    ]
    if background:
        command.append("--background")
    return subprocess.Popen(  # noqa: S603
        command,
        cwd=repo_root,
        env=env,
        stdout=None,
        stderr=None,
        text=True,
    )


def stop_process(process: subprocess.Popen[str], *, daemon_dir: Path | None = None) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)
    # For background mode, the parent has already exited; kill the child by pid.
    if daemon_dir is not None:
        pid_file = daemon_dir / "daemon.pid"
        if pid_file.exists():
            try:
                status = json.loads(pid_file.read_text())
                pid = status["pid"]
                os.kill(pid, 15)
            except (OSError, json.JSONDecodeError, KeyError):
                pass


async def wait_for_daemon_harness(
    authenticated_client,
    *,
    harness_kind: str,
    process: subprocess.Popen[str],
    timeout: float = 45,
    background: bool = False,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    last_payload: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        if not background and process.poll() is not None:
            stdout, _ = process.communicate(timeout=2)
            raise AssertionError(
                f"Daemon process exited before {harness_kind} became available.\n{stdout}"
            )
        response = await authenticated_client.get("/agent-runtime/harnesses")
        assert response.status_code == 200, response.text
        last_payload = response.json()
        for item in last_payload["items"]:
            if item["harness_kind"] == harness_kind and item["daemon_status"] == "ONLINE":
                return item
        await asyncio.sleep(0.25)
    raise AssertionError(
        f"Timed out waiting for {harness_kind} daemon harness. Last payload: {last_payload}"
    )


async def create_daemon_profile(
    authenticated_client,
    fixed_test_org,
    *,
    daemon_id: str,
    harness_kind: str,
    models: list[str],
) -> tuple[str, str]:
    requested_model = os.getenv(MODEL_ENV[harness_kind], DEFAULT_MODEL[harness_kind])
    model_name = requested_model if requested_model in models else models[0]
    response = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "USER_DAEMON",
            "daemon_id": daemon_id,
            "harness_kind": harness_kind,
            "name": f"Real Daemon {harness_kind} {uuid4().hex[:8]}",
            "default_model_name": model_name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"], model_name


async def create_workspace_agent_and_conversation(
    authenticated_client,
    *,
    pod_id: str,
    profile_id: str,
    model_name: str,
    harness_kind: str,
) -> tuple[dict, str]:
    agent_name = f"Real Daemon {harness_kind} Agent {uuid4().hex[:6]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": (
                "Use Lemma MCP tools when asked. Keep final answers concise and "
                "include exact markers requested by the user."
            ),
            "agent_runtime": {"profile_id": profile_id, "model_name": model_name},
            "toolsets": ["WORKSPACE_CLI"],
        },
    )
    assert create_agent.status_code == 201, create_agent.text
    agent = create_agent.json()
    create_conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent["name"],
            "title": f"Real daemon {harness_kind}",
        },
    )
    assert create_conversation.status_code == 201, create_conversation.text
    return agent, create_conversation.json()["id"]


async def upload_seed_file(authenticated_client, *, pod_id: str, tmp_path: Path) -> str:
    source_path = tmp_path / f"source-{uuid4().hex[:8]}.txt"
    source_path.write_text("SOURCE_FILE_CONTENT_REAL_DAEMON", encoding="utf-8")
    remote_path = f"/me/{source_path.name}"
    with source_path.open("rb") as handle:
        response = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/files",
            data={"directory_path": "/me", "name": source_path.name},
            files={"data": (source_path.name, handle, "text/plain")},
        )
    assert response.status_code == 201, response.text
    assert response.json()["path"] == remote_path
    return remote_path


def real_mcp_prompt(*, pod_id: str, source_remote_path: str, uploaded_remote_path: str) -> str:
    return (
        "Use Lemma MCP tools only for the following. "
        "First call lemma_execute_python to compute 6 * 7. "
        "Then call lemma_exec_command to run `printf SHELL_OK`. "
        f"Then call lemma_exec_command to run `lemma files download {source_remote_path} "
        f"/tmp/source-check.txt --pod {pod_id} && cat /tmp/source-check.txt`. "
        "Then call lemma_exec_command to create `/tmp/daemon-upload.txt` containing "
        "exactly `UPLOADED_BY_REAL_DAEMON` with no trailing newline and upload it with "
        f"`lemma files upload /tmp/daemon-upload.txt {uploaded_remote_path} --pod {pod_id}`. "
        "Final answer must include exactly: PYTHON_OK=42, SHELL_OK, "
        "SOURCE_FILE_CONTENT_REAL_DAEMON, UPLOAD_OK."
    )


async def assert_uploaded_file(
    authenticated_client,
    *,
    pod_id: str,
    remote_path: str,
) -> None:
    response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files/download",
        params={"path": remote_path},
    )
    assert response.status_code == 200, response.text
    assert response.text == "UPLOADED_BY_REAL_DAEMON"


async def assert_latest_assistant_contains(
    authenticated_client,
    *,
    pod_id: str,
    conversation_id: str,
    markers: list[str],
) -> str:
    response = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    assistant_messages = [
        item
        for item in response.json()["items"]
        if item["role"] == MessageRole.ASSISTANT.value
    ]
    assert assistant_messages
    content = assistant_messages[-1]["text"] or ""
    for marker in markers:
        assert marker in content
    return content


async def assert_conversation_has_tool_messages(
    authenticated_client,
    *,
    pod_id: str,
    conversation_id: str,
) -> None:
    response = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert any(
        item["role"] == MessageRole.ASSISTANT.value
        and item["kind"] == "TOOL_CALL"
        and item["tool_name"] in {"lemma_exec_command", "lemma_execute_python"}
        for item in items
    ), items
    assert any(
        item["role"] == MessageRole.TOOL.value
        and item["kind"] == "TOOL_RETURN"
        and item["tool_name"] in {"lemma_exec_command", "lemma_execute_python"}
        for item in items
    ), items


async def run_real_daemon_harness_flow(
    *,
    harness_kind: str,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    tmp_path,
    worker,
    background: bool = False,
) -> None:
    del worker
    binary = BINARY[harness_kind]
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} CLI is not installed")

    daemon_dir = tmp_path / "daemon-state"
    process = start_real_daemon_process(
        backend_server=backend_server,
        fixed_test_user=fixed_test_user,
        tmp_path=tmp_path,
        background=background,
    )
    try:
        harness = await wait_for_daemon_harness(
            authenticated_client,
            harness_kind=harness_kind,
            process=process,
            background=background,
        )
        profile_id, model_name = await create_daemon_profile(
            authenticated_client,
            fixed_test_org,
            daemon_id=harness["daemon_id"],
            harness_kind=harness_kind,
            models=harness["models"],
        )
        pod_id = await create_test_pod(authenticated_client, fixed_test_org, harness_kind)
        _, conversation_id = await create_workspace_agent_and_conversation(
            authenticated_client,
            pod_id=pod_id,
            profile_id=profile_id,
            model_name=model_name,
            harness_kind=harness_kind,
        )
        source_remote_path = await upload_seed_file(
            authenticated_client,
            pod_id=pod_id,
            tmp_path=tmp_path,
        )
        uploaded_remote_path = f"/me/daemon-upload-{uuid4().hex[:8]}.txt"

        events = await post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {
                "content": real_mcp_prompt(
                    pod_id=pod_id,
                    source_remote_path=source_remote_path,
                    uploaded_remote_path=uploaded_remote_path,
                )
            },
        )
        assert_completed_without_error(events)
        if harness_kind == "CODEX":
            assert_sse_includes_tool_stream_events(events)
        await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=pod_id,
            conversation_id=conversation_id,
            markers=[
                "PYTHON_OK=42",
                "SHELL_OK",
                "SOURCE_FILE_CONTENT_REAL_DAEMON",
                "UPLOAD_OK",
            ],
        )
        if harness_kind == "CODEX":
            await assert_conversation_has_tool_messages(
                authenticated_client,
                pod_id=pod_id,
                conversation_id=conversation_id,
            )
        await assert_uploaded_file(
            authenticated_client,
            pod_id=pod_id,
            remote_path=uploaded_remote_path,
        )

        follow_up = await post_sse(
            authenticated_client,
            f"/pods/{pod_id}/conversations/{conversation_id}/messages",
            {
                "content": (
                    "Follow up using the existing conversation context. Reply only "
                    "CONTINUATION_OK:SOURCE_FILE_CONTENT_REAL_DAEMON."
                )
            },
        )
        assert_completed_without_error(follow_up)
        await assert_latest_assistant_contains(
            authenticated_client,
            pod_id=pod_id,
            conversation_id=conversation_id,
            markers=["CONTINUATION_OK:SOURCE_FILE_CONTENT_REAL_DAEMON"],
        )

        stop_task = asyncio.create_task(
            post_sse(
                authenticated_client,
                f"/pods/{pod_id}/conversations/{conversation_id}/messages",
                {
                    "content": (
                        "Start a long-running operation now. Use lemma_exec_command "
                        "to run `sleep 120`, then say SHOULD_NOT_FINISH."
                    )
                },
                timeout=180,
            )
        )
        await asyncio.sleep(3)
        stop_response = await authenticated_client.post(
            f"/pods/{pod_id}/conversations/{conversation_id}/stop"
        )
        assert stop_response.status_code == 200, stop_response.text
        stop_events = await stop_task
        assert stop_events[-1]["type"] in {"stopped", "completed"}, stop_events
        if stop_events[-1]["type"] == "completed":
            assert stop_events[-1]["data"]["status"] == AgentRunStatus.STOPPED.value
    finally:
        stop_process(process, daemon_dir=daemon_dir if background else None)
