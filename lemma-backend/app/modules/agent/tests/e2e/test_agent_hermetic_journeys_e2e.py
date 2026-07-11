"""Required public-boundary agent journeys with deterministic model tokens."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.scripted_model import (
    script_model_error,
    script_text,
    script_tool_call,
)

pytestmark = pytest.mark.e2e

_RUNTIME_SECRET = "CANARY_AGENT_RUNTIME_SECRET_93a5"


async def _create_runtime_profile(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
) -> dict:
    response = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Hermetic FunctionModel {uuid4().hex[:8]}",
            "base_url": f"{e2e_settings.agentbox_api_url}/v1",
            "api_key": _RUNTIME_SECRET,
            "default_model_name": "mock-safe-model",
            "model_names": ["mock-safe-model"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    assert payload["has_credentials"] is True
    assert "credentials" not in payload
    assert _RUNTIME_SECRET not in response.text
    return payload


async def _create_pod(authenticated_client, fixed_test_org) -> dict:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Hermetic Agent Pod {uuid4().hex[:8]}",
            "description": "Public agent lifecycle E2E",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_mock_agent(
    authenticated_client,
    *,
    pod_id: str,
    runtime_profile_id: str,
    name_prefix: str,
) -> dict:
    agent_name = f"{name_prefix}_{uuid4().hex[:8]}"
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Use the deterministic E2E model.",
            "agent_runtime": {
                "profile_id": runtime_profile_id,
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _collect_sse(response) -> list[dict]:
    events: list[dict] = []
    async with asyncio.timeout(30):
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            events.append(event)
            if event["type"] in {"completed", "stopped", "error"}:
                break
    return events


async def _send_message(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
    content: str,
) -> list[dict]:
    url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    async with authenticated_client.stream(
        "POST",
        url,
        json={"content": content, "metadata": {"client": "hermetic-e2e"}},
        timeout=60,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        return await _collect_sse(response)


async def _send_message_with_metadata(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
    content: str,
    metadata: dict,
) -> list[dict]:
    url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    async with authenticated_client.stream(
        "POST",
        url,
        json={"content": content, "metadata": metadata},
        timeout=60,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        return await _collect_sse(response)


async def _wait_for_title(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
) -> str:
    for _ in range(100):
        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        title = response.json().get("title")
        if title:
            return str(title)
        await asyncio.sleep(0.1)
    raise AssertionError("Worker did not persist a conversation title")


async def _wait_for_usage(
    authenticated_client,
    *,
    organization_id: str,
    pod_id: str,
    agent_id: str,
    run_id: str,
) -> dict:
    for _ in range(100):
        response = await authenticated_client.get(
            f"/usage/organizations/{organization_id}/events",
            params={
                "pod_id": pod_id,
                "agent_id": agent_id,
                "usage_kind": "LLM",
                "days": 1,
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        event = next(
            (
                item
                for item in response.json()["items"]
                if item["agent_run_id"] == run_id
            ),
            None,
        )
        if event is not None:
            return event
        await asyncio.sleep(0.1)
    raise AssertionError(f"Usage for agent run {run_id} was not persisted")


@pytest.mark.asyncio
async def test_public_sse_sanitizes_provider_failure_matrix_and_persists_failure(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """Provider HTTP, protocol, quota, and unexpected failures are sanitized."""
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    agent = await _create_mock_agent(
        authenticated_client,
        pod_id=pod["id"],
        runtime_profile_id=runtime["id"],
        name_prefix="provider_failure",
    )
    canary = "CANARY_PROVIDER_EXCEPTION_SECRET_4d91"
    scenarios = (
        (
            "model_http",
            429,
            "The model provider returned an error (HTTP 429).",
        ),
        (
            "unexpected_model_behavior",
            None,
            "A tool failed repeatedly after several attempts",
        ),
        ("usage_limit", None, "The agent run hit a usage limit."),
        ("generic", None, "The model provider returned an error."),
    )

    for kind, provider_status, expected_message in scenarios:
        conversation = await authenticated_client.post(
            f"/pods/{pod['id']}/conversations",
            json={
                "agent_name": agent["name"],
                "metadata": {
                    "mock_llm_script": [
                        script_model_error(
                            kind,
                            message=f"{canary}:{kind}",
                            status_code=provider_status,
                        )
                    ]
                },
            },
        )
        assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
        conversation_id = conversation.json()["id"]
        events = await _send_message(
            authenticated_client,
            pod["id"],
            conversation_id,
            f"Trigger the {kind} provider failure.",
        )
        assert events[-1]["type"] == "error", events
        assert expected_message in str(events[-1]["data"])
        assert canary not in json.dumps(events)

        durable = await authenticated_client.get(
            f"/pods/{pod['id']}/conversations/{conversation_id}"
        )
        assert durable.status_code == status.HTTP_200_OK, durable.text
        assert durable.json()["status"] == "FAILED"
        messages = await authenticated_client.get(
            f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == status.HTTP_200_OK, messages.text
        assert canary not in messages.text


@pytest.mark.asyncio
async def test_public_sse_formats_external_context_files_state_and_email_guidance(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """External-message metadata reaches the model as clearly framed context."""
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    agent = await _create_mock_agent(
        authenticated_client,
        pod_id=pod["id"],
        runtime_profile_id=runtime["id"],
        name_prefix="external_context",
    )
    conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={"agent_name": agent["name"], "title": "External context"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    first_events = await _send_message_with_metadata(
        authenticated_client,
        pod["id"],
        conversation_id,
        "Summarize the customer request.",
        {
            "surface_platform": "OUTLOOK",
            "sender_display_name": "Ada Lovelace",
            "channel_context": [
                "ignored non-object context",
                {"author": "Grace", "text": "Earlier customer context"},
                {"author": "Empty", "text": ""},
            ],
            "attachments": [
                {
                    "name": "invoice.pdf",
                    "mime_type": "application/pdf",
                    "size": 2048,
                }
            ],
            "state": {"selected_invoice": "INV-42", "tab": "review"},
        },
    )
    assert first_events[-1]["type"] == "completed", first_events

    second_events = await _send_message_with_metadata(
        authenticated_client,
        pod["id"],
        conversation_id,
        "Review the files saved from the follow-up.",
        {
            "surface_platform": "OUTLOOK",
            "sender_email": "ada@example.test",
            "ingested_files": ["/surface/follow-up.md", "/surface/chart.png"],
        },
    )
    assert second_events[-1]["type"] == "completed", second_events

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    assistant_text = "\n".join(
        str(item.get("text") or "")
        for item in messages.json()["items"]
        if item["role"] == "assistant"
    )
    for expected in (
        "OUTLOOK | Ada Lovelace",
        "Earlier customer context",
        "invoice.pdf",
        "INV-42",
        "/surface/follow-up.md",
        "/surface/chart.png",
    ):
        assert expected in assistant_text


@pytest.mark.asyncio
async def test_public_runtime_profile_anthropic_discovery_and_validation_matrix(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
):
    """Provider profiles discover models and reject unsafe or unusable config."""
    canary = "CANARY_ANTHROPIC_PROFILE_KEY_b628"
    created = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "ANTHROPIC_COMPATIBLE",
            "name": f"Anthropic compatible {uuid4().hex[:8]}",
            "base_url": f"{e2e_settings.agentbox_api_url}/v1",
            "api_key": canary,
            "default_model_name": "mock-safe-model",
            "headers": {"X-E2E-Tenant": "runtime-profile"},
            "model_settings": {"temperature": 0},
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    profile = created.json()
    assert profile["protocol"] == "ANTHROPIC_COMPATIBLE"
    assert profile["default_model_name"] == "mock-safe-model"
    assert profile["has_credentials"] is True
    assert canary not in created.text
    model = next(
        item for item in profile["model_catalog"] if item["name"] == "mock-safe-model"
    )
    assert set(model["capabilities"]) == {"TEXT", "TOOLS", "VISION"}

    listed = await authenticated_client.get(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert profile["id"] in {item["id"] for item in listed.json()["items"]}
    assert canary not in listed.text

    invalid_default = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": "Missing default model",
            "base_url": f"{e2e_settings.agentbox_api_url}/v1",
            "api_key": "not-persisted",
            "default_model_name": "model-that-was-not-discovered",
        },
    )
    assert invalid_default.status_code == status.HTTP_400_BAD_REQUEST
    assert "provider model names" in invalid_default.json()["message"]

    empty_catalog = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": "Empty provider catalog",
            "base_url": f"{e2e_settings.agentbox_api_url}/missing",
            "api_key": "not-persisted",
        },
    )
    assert empty_catalog.status_code == status.HTTP_400_BAD_REQUEST
    assert "provide model_names" in empty_catalog.json()["message"]

    unsafe_url = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": "Cloud metadata is forbidden",
            "base_url": "http://169.254.169.254/latest",
            "api_key": "CANARY_SSRF_KEY_must_not_leak",
            "model_names": ["fallback-model"],
        },
    )
    assert unsafe_url.status_code == status.HTTP_400_BAD_REQUEST
    assert unsafe_url.json()["message"] == "base_url must be a public http(s) URL"
    assert "CANARY_SSRF_KEY" not in unsafe_url.text

    unavailable_daemon = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "USER_DAEMON",
            "daemon_id": str(uuid4()),
            "harness_kind": "CODEX",
            "name": "Unavailable laptop",
        },
    )
    assert unavailable_daemon.status_code == status.HTTP_400_BAD_REQUEST
    assert "not available" in unavailable_daemon.json()["message"]


@pytest.mark.asyncio
async def test_public_http_sse_lifecycle_persists_messages_title_usage_and_history(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    e2e_settings,
    worker,
):
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"lifecycle_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Reply using the scripted deterministic model.",
            "description": "Hermetic lifecycle agent",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
            "metadata": {"suite": "required-e2e"},
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    duplicate = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": agent_name, "instruction": "duplicate"},
    )
    assert duplicate.status_code == status.HTTP_409_CONFLICT, duplicate.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "instructions": "Use current UI context.",
            "metadata": {
                "mock_llm_script": [script_text("Hermetic lifecycle complete.")],
                "source": "public-http-e2e",
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Verify the complete public lifecycle.",
    )
    assert events, "SSE returned no frames"
    assert events[-1]["type"] == "completed", events
    assert not [event for event in events if event["type"] == "error"], events
    token_text = "".join(
        str(event.get("data", ""))
        for event in events
        if event["type"] == "token" and event.get("kind") == "text"
    )
    assert "Hermetic lifecycle complete" in token_text
    run_id = events[-1]["agent_run_id"]

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]
    assert [item["sequence"] for item in items] == sorted(
        item["sequence"] for item in items
    )
    assert any(
        item["role"] == "user"
        and item["text"] == "Verify the complete public lifecycle."
        and item["metadata"]["client"] == "hermetic-e2e"
        for item in items
    )
    assert any(
        item["role"] == "assistant"
        and item["text"] == "Hermetic lifecycle complete."
        and item["metadata"].get("is_final_answer")
        for item in items
    )

    title = await _wait_for_title(authenticated_client, pod_id, conversation_id)
    assert title == "Verify the complete public lifecycle."
    usage = await _wait_for_usage(
        authenticated_client,
        organization_id=fixed_test_org["id"],
        pod_id=pod_id,
        agent_id=agent["id"],
        run_id=run_id,
    )
    assert usage["conversation_id"] == conversation_id
    assert usage["user_id"] == fixed_test_user["id"]
    assert usage["status"] == "COMPLETED"

    idle_stream = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/stream"
    )
    assert idle_stream.status_code == status.HTTP_200_OK, idle_stream.text
    assert idle_stream.content == b""

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"agent_name": agent_name, "metadata.source": "public-http-e2e"},
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [conversation_id]

    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/conversations/{conversation_id}",
        json={"instructions": "Updated after the completed run."},
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["instructions"] == "Updated after the completed run."

    deleted = await authenticated_client.delete(f"/pods/{pod_id}/agents/{agent_name}")
    assert deleted.status_code == status.HTTP_200_OK, deleted.text
    missing = await authenticated_client.get(f"/pods/{pod_id}/agents/{agent_name}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND, missing.text


@pytest.mark.asyncio
async def test_scripted_todo_and_workspace_tools_stream_and_persist_real_results(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"tools_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Execute the scripted tools.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [
                "TODO",
                "WORKSPACE_CLI",
                "VIEW_IMAGE",
                "SKILLS",
                "SPEECH",
            ],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    script = [
        script_tool_call(
            "write_todos",
            {"todos": ["- [ ] Inspect input", "- [x] Persist result"]},
            tool_call_id="todo-1",
        ),
        script_tool_call(
            "exec_command",
            {
                "cmd": "printf 'workspace-proof' > proof.txt && cat proof.txt",
                "comment": "Create deterministic workspace proof",
            },
            tool_call_id="shell-1",
        ),
        script_tool_call(
            "exec_command",
            {
                "cmd": "printf 'tty-proof'",
                "tty": True,
                "yield_time_ms": 10,
                "comment": "Exercise the interactive command contract",
            },
            tool_call_id="shell-tty-1",
        ),
        script_tool_call(
            "exec_command",
            {
                "cmd": "printf 'blocking-proof'",
                "timeout_seconds": 10,
                "comment": "Exercise the blocking timeout contract",
            },
            tool_call_id="shell-blocking-1",
        ),
        script_tool_call(
            "manage_process",
            {
                "action": "input",
                "process_id": "fake-interactive-process",
                "chars": "status\n",
                "yield_time_ms": 10,
            },
            tool_call_id="process-input-1",
        ),
        script_tool_call(
            "manage_process",
            {
                "action": "kill",
                "process_id": "fake-interactive-process",
                "comment": "Stop the deterministic process",
            },
            tool_call_id="process-kill-1",
        ),
        script_tool_call(
            "execute_python",
            {
                "code": (
                    "import base64\n"
                    "from pathlib import Path\n"
                    "Path('pixel.png').write_bytes(base64.b64decode("
                    "'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII='))\n"
                    "print(21 * 2)"
                ),
                "comment": "Create an inspectable image and compute a value",
            },
            tool_call_id="python-1",
        ),
        script_tool_call(
            "execute_python",
            {
                "code": "raise RuntimeError('scripted user-code failure')",
                "comment": "Exercise a user-code failure",
            },
            tool_call_id="python-failure-1",
        ),
        script_tool_call(
            "view_image",
            {"workspace_file_path": "pixel.png"},
            tool_call_id="image-1",
        ),
        script_tool_call(
            "view_image",
            {},
            tool_call_id="image-path-required-1",
        ),
        script_tool_call(
            "view_image",
            {"workspace_file_path": "proof.txt"},
            tool_call_id="image-type-invalid-1",
        ),
        script_tool_call(
            "manage_process",
            {"action": "list", "comment": "Check tracked processes"},
            tool_call_id="process-list-1",
        ),
        script_tool_call(
            "manage_process",
            {"action": "input", "chars": ""},
            tool_call_id="process-invalid-1",
        ),
        script_tool_call(
            "exec_command",
            {"cmd": "exit 7", "comment": "Exercise a user-command failure"},
            tool_call_id="shell-failure-1",
        ),
        script_tool_call("list_skills", {}, tool_call_id="skills-list-1"),
        script_tool_call(
            "load_skill",
            {"name": "browser"},
            tool_call_id="skill-load-1",
        ),
        script_tool_call(
            "load_skill",
            {"name": "does-not-exist"},
            tool_call_id="skill-missing-1",
        ),
        script_tool_call(
            "say",
            {"text": "Hermetic spoken response"},
            tool_call_id="speech-say-1",
        ),
        script_tool_call(
            "listen",
            {"file_path": "missing-audio.ogg"},
            tool_call_id="speech-listen-1",
        ),
        script_text("Todo, workspace, skills, and speech tools completed."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Tool execution",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Run the todo and workspace proof steps.",
    )
    assert events[-1]["type"] == "completed", events
    assert {event.get("kind") for event in events if event["type"] == "token"} >= {
        "text",
        "tool",
    }

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]
    tool_calls = {
        item["tool_name"]: item for item in items if item["kind"] == "TOOL_CALL"
    }
    tool_returns = {
        item["tool_name"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }
    tool_returns_by_id = {
        item["tool_call_id"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }
    assert {
        "write_todos",
        "exec_command",
        "execute_python",
        "view_image",
        "manage_process",
        "list_skills",
        "load_skill",
        "say",
        "listen",
    } <= tool_calls.keys()
    assert tool_returns["write_todos"]["tool_result"]["success"] is True
    assert "workspace-proof" in str(tool_returns_by_id["shell-1"]["tool_result"])
    assert "tty-proof" in str(tool_returns_by_id["shell-tty-1"]["tool_result"])
    assert "blocking-proof" in str(
        tool_returns_by_id["shell-blocking-1"]["tool_result"]
    )
    assert tool_returns_by_id["shell-failure-1"]["tool_result"]["success"] is False
    assert tool_returns_by_id["process-input-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["process-kill-1"]["tool_result"]["success"] is True
    assert "42" in str(tool_returns_by_id["python-1"]["tool_result"])
    assert tool_returns_by_id["python-failure-1"]["tool_result"]["success"] is False
    assert tool_returns_by_id["image-1"]["tool_result"]
    assert (
        tool_returns_by_id["image-path-required-1"]["tool_result"]["success"] is False
    )
    assert tool_returns_by_id["image-type-invalid-1"]["tool_result"]["success"] is False
    assert tool_returns["list_skills"]["tool_result"]["success"] is True
    assert tool_returns_by_id["skill-load-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["skill-missing-1"]["tool_result"]["success"] is False
    assert tool_returns_by_id["process-list-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["process-invalid-1"]["tool_result"]["success"] is False
    assert tool_returns["say"]["tool_result"]["success"] is False
    assert tool_returns["listen"]["tool_result"]["success"] is False

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    todos = persisted.json()["metadata"]["todos"]
    assert todos == [
        {"content": "Inspect input", "done": False},
        {"content": "Persist result", "done": True},
    ]


@pytest.mark.asyncio
async def test_scripted_pod_data_and_file_tools_cross_worker_authorization_boundaries(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """Use every pod data/file tool from the public conversation boundary."""
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    owner = DatastoreApi(authenticated_client, pod_id)
    table_name = f"agent_notes_{uuid4().hex[:8]}"
    await owner.create_table(
        {
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        }
    )
    seeded_record = await owner.create_record(table_name, {"title": "seeded"})
    record_id = seeded_record["id"]
    root = f"/agent-tool-e2e-{uuid4().hex[:8]}"
    await owner.create_folder(root)
    await owner.upload_file(
        "seed.md",
        b"Seeded public-boundary file",
        directory_path=root,
        search_enabled=False,
    )

    agent_name = f"pod_tools_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Use the scripted pod tools.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["POD"],
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    grants = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_name,
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "datastore_table",
                    "resource_name": table_name,
                    "permission_ids": [
                        "datastore.table.read",
                        "datastore.record.read",
                        "datastore.record.write",
                    ],
                },
                {
                    "resource_type": "folder",
                    "resource_name": root,
                    "permission_ids": ["folder.read", "folder.write"],
                },
            ]
        },
    )
    assert grants.status_code == status.HTTP_200_OK, grants.text

    script = [
        script_tool_call("pod_tables", {}, tool_call_id="tables-list"),
        script_tool_call(
            "pod_tables",
            {"table_name": table_name},
            tool_call_id="tables-get",
        ),
        script_tool_call(
            "pod_get_records",
            {
                "table_name": table_name,
                "filters": [{"column": "title", "op": "eq", "value": "seeded"}],
                "sorts": [{"column": "title", "direction": "desc"}],
            },
            tool_call_id="records-list",
        ),
        script_tool_call(
            "pod_get_records",
            {"table_name": table_name, "record_id": record_id},
            tool_call_id="record-get",
        ),
        script_tool_call(
            "pod_write_record",
            {
                "action": "create",
                "table_name": table_name,
                "data": '{"title":"created by model"}',
            },
            tool_call_id="record-create",
        ),
        script_tool_call(
            "pod_write_record",
            {
                "action": "update",
                "table_name": table_name,
                "record_id": record_id,
                "data": {"title": "updated by model"},
            },
            tool_call_id="record-update",
        ),
        script_tool_call(
            "pod_write_record",
            {
                "action": "delete",
                "table_name": table_name,
                "record_id": record_id,
            },
            tool_call_id="record-delete",
        ),
        script_tool_call(
            "pod_write_record",
            {"action": "update", "table_name": table_name, "data": {}},
            tool_call_id="record-invalid",
        ),
        script_tool_call(
            "pod_query",
            {"sql": f'SELECT title FROM "{table_name}" ORDER BY title'},
            tool_call_id="query-readonly",
        ),
        script_tool_call(
            "pod_write_file",
            {"path": f"{root}/created.md", "content": "first version"},
            tool_call_id="file-create",
        ),
        script_tool_call(
            "pod_write_file",
            {
                "path": f"{root}/created.md",
                "content": "must not replace",
                "overwrite": False,
            },
            tool_call_id="file-conflict",
        ),
        script_tool_call(
            "pod_write_file",
            {"path": f"{root}/created.md", "content": "replacement version"},
            tool_call_id="file-overwrite",
        ),
        script_tool_call(
            "pod_list_files",
            {"path": root},
            tool_call_id="files-list",
        ),
        script_tool_call(
            "pod_list_files",
            {"path": root, "recursive": True},
            tool_call_id="files-tree",
        ),
        script_tool_call(
            "pod_read_file",
            {"path": f"{root}/created.md", "format": "text", "max_chars": 100},
            tool_call_id="file-read",
        ),
        script_tool_call(
            "pod_get_file_url",
            {"path": f"{root}/created.md", "url_type": "app"},
            tool_call_id="file-app-url",
        ),
        script_tool_call(
            "pod_get_file_url",
            {
                "path": f"{root}/created.md",
                "url_type": "public",
                "expires_seconds": 60,
                "max_hits": 2,
            },
            tool_call_id="file-public-url",
        ),
        script_tool_call(
            "pod_search_files",
            {"query": "replacement", "method": "TEXT", "scope_path": root},
            tool_call_id="files-search",
        ),
        script_tool_call(
            "pod_view_document_pages",
            {"path": f"{root}/created.md", "page_start": 1},
            tool_call_id="file-pages-invalid",
        ),
        script_text("Pod records and files completed."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Pod tool boundary",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Exercise authorized pod data and file operations.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in messages.json()["items"]
        if item["kind"] == "TOOL_RETURN"
    }
    for tool_call_id in (
        "tables-list",
        "tables-get",
        "records-list",
        "record-get",
        "record-create",
        "record-update",
        "record-delete",
        "query-readonly",
        "file-create",
        "file-overwrite",
        "files-list",
        "files-tree",
        "file-read",
        "file-app-url",
        "file-public-url",
        "files-search",
    ):
        assert returns[tool_call_id]["success"] is True, (tool_call_id, returns)
    assert returns["record-invalid"]["success"] is False
    assert returns["file-conflict"]["success"] is False
    assert returns["file-pages-invalid"]["success"] is False
    assert returns["record-create"]["record"]["title"] == "created by model"
    assert returns["record-update"]["record"]["title"] == "updated by model"
    assert returns["record-delete"]["deleted"] is True
    assert returns["file-overwrite"]["created"] is False
    assert returns["file-read"]["text"] == "replacement version"
    assert returns["file-public-url"]["max_hits"] == 2

    records = await owner.list_records(table_name)
    assert [item["title"] for item in records["items"]] == ["created by model"]
    file_content = await owner.download_file(f"{root}/created.md")
    assert file_content == b"replacement version"


@pytest.mark.asyncio
async def test_dynamic_function_and_agent_tools_create_durable_child_runs(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """Invoke granted functions and agents through the generated tool schemas."""
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]

    function_name = f"callable_{uuid4().hex[:8]}"
    source = f"""#input_type_name: FunctionInput
#output_type_name: FunctionOutput
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class FunctionInput(BaseModel):
    value: str

class FunctionOutput(BaseModel):
    value: str

async def {function_name}(
    ctx: FunctionContext, data: FunctionInput
) -> FunctionOutput:
    return FunctionOutput(value=data.value)
"""
    created_function = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={
            "name": function_name,
            "description": "Public dynamic callable E2E",
            "code": source,
        },
    )
    assert created_function.status_code == status.HTTP_201_CREATED, (
        created_function.text
    )
    async with httpx.AsyncClient(base_url=e2e_settings.agentbox_api_url) as client:
        configured = await client.post(
            "/__test__/function-executor/configure",
            json={"modes": ["success"], "log_message": "dynamic function completed"},
        )
    assert configured.status_code == status.HTTP_200_OK, configured.text

    child_name = f"child_{uuid4().hex[:8]}"
    child = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": child_name,
            "instruction": "Return the delegated input briefly.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert child.status_code == status.HTTP_201_CREATED, child.text

    parent_name = f"parent_{uuid4().hex[:8]}"
    parent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": parent_name,
            "instruction": "Invoke the two scripted dynamic tools.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert parent.status_code == status.HTTP_201_CREATED, parent.text
    permissions = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{parent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": function_name,
                    "permission_ids": ["function.execute"],
                },
                {
                    "resource_type": "agent",
                    "resource_name": child_name,
                    "permission_ids": ["agent.execute"],
                },
            ]
        },
    )
    assert permissions.status_code == status.HTTP_200_OK, permissions.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": parent_name,
            "title": "Dynamic callable tools",
            "metadata": {
                "mock_llm_script": [
                    script_tool_call(
                        f"function_{function_name}",
                        {"value": "function input"},
                        tool_call_id="dynamic-function",
                    ),
                    script_tool_call(
                        f"agent_{child_name}",
                        {"input": "delegated child input"},
                        tool_call_id="dynamic-agent",
                    ),
                    script_text("Dynamic function and child agent completed."),
                ]
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Invoke the configured function and child agent.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in messages.json()["items"]
        if item["kind"] == "TOOL_RETURN"
    }
    assert returns["dynamic-function"] == {
        "echo": {"value": "function input"},
        "function": function_name,
    }
    assert "delegated child input" in str(returns["dynamic-agent"])

    children = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"parent_id": conversation_id},
    )
    assert children.status_code == status.HTTP_200_OK, children.text
    child_items = children.json()["items"]
    assert len(child_items) == 1
    assert child_items[0]["parent_id"] == conversation_id
    child_detail = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{child_items[0]['id']}"
    )
    assert child_detail.status_code == status.HTTP_200_OK, child_detail.text
    assert child_detail.json()["status"] == "COMPLETED"
