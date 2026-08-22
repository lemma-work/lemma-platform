"""Required public-boundary agent journeys with deterministic model tokens."""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.scripted_model import (
    script_model_error,
    script_text,
    script_tool_call,
    script_tool_result_ref,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_RUNTIME_SECRET = "CANARY_AGENT_RUNTIME_SECRET_93a5"
# Never dialled: these journeys run against a mock model, so the profile only
# needs a well-formed URL the API will accept.
_UNUSED_MODEL_BASE_URL = "http://127.0.0.1:9"


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
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
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
    toolsets: list[str] | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> dict:
    agent_name = f"{name_prefix}_{uuid4().hex[:8]}"
    body = {
        "name": agent_name,
        "instruction": "Use the deterministic E2E model.",
        "agent_runtime": {
            "profile_id": runtime_profile_id,
            "model_name": "mock-safe-model",
        },
        "toolsets": toolsets or [],
    }
    if input_schema is not None:
        body["input_schema"] = input_schema
    if output_schema is not None:
        body["output_schema"] = output_schema
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json=body,
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
    async def probe() -> dict:
        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        return response.json()

    payload = await eventually(
        label="Worker to persist a conversation title",
        probe=probe,
        done=lambda body: bool(body.get("title")),
        timeout_seconds=10.0,
        interval_seconds=0.1,
    )
    return str(payload["title"])


async def _wait_for_usage(
    authenticated_client,
    *,
    organization_id: str,
    pod_id: str,
    agent_id: str,
    run_id: str,
) -> dict:
    async def probe() -> dict | None:
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
        return next(
            (
                item
                for item in response.json()["items"]
                if item["agent_run_id"] == run_id
            ),
            None,
        )

    return await eventually(
        label=f"usage for agent run {run_id} to be persisted",
        probe=probe,
        done=lambda event: event is not None,
        timeout_seconds=10.0,
        interval_seconds=0.1,
    )


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
            # Rate limiting is retried, so the message says "try again shortly"
            # rather than sending the reader to the runtime configuration.
            "rate limiting this workspace (HTTP 429)",
        ),
        (
            "model_http",
            402,
            "rejected the request for billing reasons (HTTP 402)",
        ),
        (
            "model_http",
            503,
            "having trouble (HTTP 503)",
        ),
        (
            "model_http",
            418,
            "The model provider returned an error (HTTP 418)",
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
    monkeypatch,
):
    """Provider profiles discover models and reject unsafe or unusable config."""
    from app.modules.agent.services.runtime_provider_discovery import (
        DiscoveredModel,
        _validate_public_base_url,
    )

    async def discover_anthropic_models(**_kwargs):
        return [DiscoveredModel("mock-safe-model", supports_vision=True)]

    async def discover_openai_models(*, base_url: str, **_kwargs):
        await _validate_public_base_url(base_url)
        if base_url.endswith("/missing"):
            return []
        return [DiscoveredModel("mock-safe-model", supports_vision=True)]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_anthropic_compatible_models",
        discover_anthropic_models,
    )
    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_openai_compatible_models",
        discover_openai_models,
    )
    canary = "CANARY_ANTHROPIC_PROFILE_KEY_b628"
    created = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "ANTHROPIC_COMPATIBLE",
            "name": f"Anthropic compatible {uuid4().hex[:8]}",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
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
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
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
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/missing",
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

    unavailable_harness = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "AGENT_HOST",
            "harness_id": str(uuid4()),
            "name": "Unavailable laptop",
        },
    )
    assert unavailable_harness.status_code == status.HTTP_400_BAD_REQUEST
    assert "not available" in unavailable_harness.json()["message"]


@pytest.mark.asyncio
async def test_public_runtime_profile_discovery_talks_to_a_real_http_endpoint(
    authenticated_client,
    fixed_test_org,
):
    """`_discover_openai_compatible_models`, driven against a real HTTP
    endpoint rather than a monkeypatched stand-in.

    Every other discovery test in this file mocks the discovery *function*
    itself (a legitimate choice for testing the surrounding validation logic),
    so `_discover_models`'s real httpx call, the real Authorization header,
    and the real OpenAI-compatible JSON parsing -- including OpenRouter-style
    vision detection from `architecture.input_modalities` -- have never run
    for real anywhere in the suite. Only the third party on the other end (a
    provider's `/models` endpoint) is faked here; the client, the request, and
    the parser are the genuine article, reached over a real socket.
    """
    import json as jsonlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = jsonlib.dumps(
        {
            "data": [
                {"id": "fake-text-model"},
                {
                    "id": "fake-vision-model",
                    "architecture": {"input_modalities": ["text", "image"]},
                },
                # A malformed entry (no id/name) must be skipped, not crash
                # the parse of everything after it.
                {"architecture": None},
            ]
        }
    ).encode()
    captured: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            captured.append({"path": self.path, "headers": dict(self.headers)})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):  # silence the default access log
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        canary = "CANARY_REAL_DISCOVERY_KEY_9c31"
        created = await authenticated_client.post(
            f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
            json={
                "source": "OPENAI_COMPATIBLE",
                "name": f"Real discovery {uuid4().hex[:8]}",
                "base_url": f"http://127.0.0.1:{port}/v1",
                "api_key": canary,
            },
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text
        assert canary not in created.text
        profile = created.json()
        catalog_by_name = {m["name"]: m for m in profile["model_catalog"]}
        assert set(catalog_by_name) == {"fake-text-model", "fake-vision-model"}
        assert "VISION" not in catalog_by_name["fake-text-model"]["capabilities"]
        assert "VISION" in catalog_by_name["fake-vision-model"]["capabilities"]
        # The auto-picked default is the first entry discovery returned.
        assert profile["default_model_name"] == "fake-text-model"
        # The real request landed with the real key, at the real joined path.
        assert any(
            call["path"] == "/v1/models"
            and call["headers"].get("Authorization") == f"Bearer {canary}"
            for call in captured
        ), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
@pytest.mark.fast_workspace
@pytest.mark.fast_workspace
@pytest.mark.timeout(300)
async def test_scripted_tool_calls_reach_the_runtime_and_persist(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    """A scripted tool call reaches the real runtime, streams back, and persists.

    The PR-lane half of the 23-step journey below. That test measured 97.7s --
    the slowest in the entire e2e suite and the only one left above the 45s
    per-test budget -- because it scripts twenty-three tool calls in strict
    sequence through one real Docker workspace.

    The guarantee it uniquely carries needs three of them. `write_todos` is the
    only step whose side effect lands in conversation *metadata* rather than in
    the sandbox. `exec_command` proves the shell transport carries a real result
    back. `execute_python` is a genuinely different transport -- a resident
    interpreter session rather than a command exec -- not a repeat of the shell.

    Everything else there is tool-surface variation: tty and process lifecycle,
    image inspection, skills, speech, web fetch. Those cannot break without this
    failing too, so they run nightly instead of in front of the merge button.
    """
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
            "toolsets": ["TODO", "WORKSPACE_CLI"],
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
            "execute_python",
            {
                "code": "print(21 * 2)",
                "comment": "Compute a value in the resident interpreter",
            },
            tool_call_id="python-1",
        ),
        script_text("Tool proof steps completed."),
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
    tool_calls = {item["tool_name"] for item in items if item["kind"] == "TOOL_CALL"}
    tool_returns_by_id = {
        item["tool_call_id"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }
    assert {"write_todos", "exec_command", "execute_python"} <= tool_calls
    assert tool_returns_by_id["todo-1"]["tool_result"]["success"] is True
    assert "workspace-proof" in str(tool_returns_by_id["shell-1"]["tool_result"])
    assert "42" in str(tool_returns_by_id["python-1"]["tool_result"])

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    assert persisted.json()["metadata"]["todos"] == [
        {"content": "Inspect input", "done": False},
        {"content": "Persist result", "done": True},
    ]


# @pytest.mark.slow: the exhaustive tool-surface matrix, moved off the PR lane.
# It scripts twenty-three tool calls in strict sequence through one real Docker
# workspace and measured 97.7s -- the slowest test in the suite. The wiring it
# uniquely proves is covered above in ~20s; what is left here is variation
# (tty/process lifecycle, image inspection, skills, speech, web fetch), which
# cannot break without that test failing too.
#
# Moving it also takes a live `https://example.com/` fetch out of the required
# merge lane -- a real outbound network call, in a file whose docstring says
# "hermetic", against docs/testing.md's rule that a test must not depend on the
# machine it runs on.
@pytest.mark.slow
@pytest.mark.timeout(300)
async def test_scripted_todo_and_workspace_tools_stream_and_persist_real_results(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    del worker, configure_workspace_api_url
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
                "WEB_SEARCH",
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
                # `cat` echoes whatever it is sent and stays alive until killed,
                # so the input and kill calls below have a real process to drive
                # and its echo proves the characters actually arrived.
                "cmd": "printf 'tty-proof\\n' && cat",
                "tty": True,
                "yield_time_ms": 200,
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
                "process_id": script_tool_result_ref("shell-tty-1", "process_id"),
                "chars": "status\n",
                "yield_time_ms": 500,
            },
            tool_call_id="process-input-1",
        ),
        script_tool_call(
            "manage_process",
            {
                "action": "kill",
                "process_id": script_tool_result_ref("shell-tty-1", "process_id"),
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
        script_tool_call(
            "exec_command",
            {
                "cmd": "git status",
                "comment": "Exercise the GitHub credential bridge gate",
            },
            tool_call_id="shell-git-1",
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
            "load_skill",
            {"name": "browser", "resource_path": "references/agent-browser-core.md"},
            tool_call_id="skill-resource-1",
        ),
        script_tool_call(
            "load_skill",
            {"name": "browser", "resource_path": "../../etc/passwd"},
            tool_call_id="skill-resource-traversal-1",
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
        script_tool_call(
            "web_fetch",
            {
                "urls": ["https://example.com/"],
                "formats": ["markdown"],
                "out_dir": "research",
                "comment": "Capture a deterministic public page",
            },
            tool_call_id="web-fetch-1",
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
        "web_fetch",
    } <= tool_calls.keys()
    assert tool_returns["write_todos"]["tool_result"]["success"] is True
    assert "workspace-proof" in str(tool_returns_by_id["shell-1"]["tool_result"])
    assert "tty-proof" in str(tool_returns_by_id["shell-tty-1"]["tool_result"])
    assert "blocking-proof" in str(
        tool_returns_by_id["shell-blocking-1"]["tool_result"]
    )
    assert tool_returns_by_id["shell-failure-1"]["tool_result"]["success"] is False
    # No GitHub account is connected in this test org, so the bridge resolves
    # to "unavailable" and the command runs uncredentialed -- git reports its
    # own native error, but the run must not crash going through the gate.
    git_result = tool_returns_by_id["shell-git-1"]["tool_result"]
    assert git_result["completed"] is True
    # These drive the process `shell-tty-1` actually started, by the id it
    # actually returned. Asserting only `success` would pass against a process
    # that ignored the input, so require the echo back from `cat`.
    process_input = tool_returns_by_id["process-input-1"]["tool_result"]
    assert process_input["success"] is True
    assert "status" in str(process_input)
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
    skill_resource = tool_returns_by_id["skill-resource-1"]["tool_result"]
    assert skill_resource["success"] is True
    assert skill_resource["resource_path"] == "references/agent-browser-core.md"
    assert "agent-browser core" in str(skill_resource["content"])
    skill_resource_traversal = tool_returns_by_id["skill-resource-traversal-1"][
        "tool_result"
    ]
    assert skill_resource_traversal["success"] is False
    assert tool_returns_by_id["process-list-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["process-invalid-1"]["tool_result"]["success"] is False
    assert tool_returns["say"]["tool_result"]["success"] is False
    assert tool_returns["listen"]["tool_result"]["success"] is False
    assert tool_returns["web_fetch"]["tool_result"]["success"] is True

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
@pytest.mark.fast_workspace
@pytest.mark.timeout(300)
async def test_the_shell_and_python_share_the_conversations_one_directory(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    """One conversation is one directory, whichever tool the agent reaches for.

    The conversation's metadata is the single source of truth for where it
    works. `exec_command` honoured it and `execute_python` did not: on a
    provider with no resident interpreter the cwd was only ever passed when the
    session was created, and the E2B provider dropped it there and started each
    execution wherever the sandbox image defaults to. The agent saw `pwd` and
    `os.getcwd()` disagree, and a file one tool wrote by relative path was
    invisible to the other -- which reads, from inside the run, as work
    vanishing.

    So this asserts the two facts that make the directory real: both tools
    report the path metadata records, and each one can read what the other
    wrote by relative name.
    """
    del worker, configure_workspace_api_url
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"cwd_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Report the working directory.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["WORKSPACE_CLI"],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    script = [
        script_tool_call(
            "exec_command",
            {"cmd": "pwd", "comment": "Where the shell is"},
            tool_call_id="shell-pwd-1",
        ),
        script_tool_call(
            "execute_python",
            {
                "code": "import os\nprint(os.getcwd())",
                "comment": "Where the interpreter is",
            },
            tool_call_id="python-cwd-1",
        ),
        script_tool_call(
            "execute_python",
            {
                # Relative on purpose: an absolute path would pass even with the
                # interpreter sitting in the wrong directory, which is exactly
                # the bug this is here to catch.
                "code": (
                    "from pathlib import Path\n"
                    "Path('python-wrote.txt').write_text('python-was-here')"
                ),
                "comment": "Write a file by relative name",
            },
            tool_call_id="python-write-1",
        ),
        script_tool_call(
            "exec_command",
            {"cmd": "cat python-wrote.txt", "comment": "Read what Python wrote"},
            tool_call_id="shell-read-1",
        ),
        script_tool_call(
            "exec_command",
            {
                "cmd": "printf 'shell-was-here' > shell-wrote.txt",
                "comment": "Write a file by relative name",
            },
            tool_call_id="shell-write-1",
        ),
        script_tool_call(
            "execute_python",
            {
                "code": (
                    "from pathlib import Path\n"
                    "print(Path('shell-wrote.txt').read_text())"
                ),
                "comment": "Read what the shell wrote",
            },
            tool_call_id="python-read-1",
        ),
        script_text("Working directory confirmed."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Working directory",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    # Creation stamps the directory; everything below must agree with it rather
    # than with any path recomputed on the side.
    recorded_cwd = conversation.json()["metadata"]["cwd"]
    assert recorded_cwd.startswith("/workspace/"), conversation.json()["metadata"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Say where you are working.",
    )
    assert events[-1]["type"] == "completed", events

    async def _returns_by_id() -> dict[str, list[dict]]:
        messages = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}/messages"
        )
        assert messages.status_code == status.HTTP_200_OK, messages.text
        grouped: dict[str, list[dict]] = {}
        for item in messages.json()["items"]:
            if item["kind"] == "TOOL_RETURN":
                grouped.setdefault(item["tool_call_id"], []).append(item["tool_result"])
        return grouped

    returns = await _returns_by_id()

    shell_cwd = (returns["shell-pwd-1"][0]["stdout"] or "").strip()
    python_cwd = (returns["python-cwd-1"][0]["stdout"] or "").strip()
    assert shell_cwd == recorded_cwd, returns["shell-pwd-1"]
    assert python_cwd == recorded_cwd, returns["python-cwd-1"]

    assert returns["python-write-1"][0]["success"] is True, returns["python-write-1"]
    assert "python-was-here" in str(returns["shell-read-1"][0]), returns["shell-read-1"]
    assert returns["shell-write-1"][0]["success"] is True, returns["shell-write-1"]
    assert "shell-was-here" in str(returns["python-read-1"][0]), returns[
        "python-read-1"
    ]

    # A second turn in the same conversation, because the directory is a
    # property of the conversation rather than of a run. Anything that
    # recomputed it per run -- a default, a fresh slug, a fallback -- would move
    # house here and leave the first turn's files behind, which is the failure
    # the recorded cwd exists to prevent.
    second = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Where are you working now?",
    )
    assert second[-1]["type"] == "completed", second

    returns = await _returns_by_id()
    assert len(returns["shell-pwd-1"]) == 2, returns["shell-pwd-1"]
    assert (returns["shell-pwd-1"][1]["stdout"] or "").strip() == recorded_cwd
    assert (returns["python-cwd-1"][1]["stdout"] or "").strip() == recorded_cwd
    # And the previous turn's files are still under it, read by relative name.
    assert "shell-was-here" in str(returns["python-read-1"][1])


@pytest.mark.asyncio
@pytest.mark.fast_workspace
@pytest.mark.timeout(300)
async def test_a_project_conversation_is_checked_out_before_python_runs(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
    configure_workspace_api_url,
):
    """Picking a project is picking a directory -- for both tools, not one.

    A conversation started against a repo resolves its cwd to
    `/workspace/repos/{owner}/{repo}`, and `get_session` creates that directory
    whether or not anything was ever cloned into it. Only `exec_command` ran the
    checkout, so an agent whose first tool call was `execute_python` opened its
    project, found an empty folder, and was told nothing about why.

    The repo here cannot exist, so the clone fails for a stable reason and the
    assertion is on the part that matters: the agent is *told*, on the Python
    result, rather than left to infer something from an empty directory.
    """
    del worker, configure_workspace_api_url
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"project_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Work in the project.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["WORKSPACE_CLI"],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    owner = "lemma-work"
    repo = f"no-such-repo-{uuid4().hex[:12]}"
    script = [
        # Python first, deliberately: this is the order that used to skip the
        # checkout entirely.
        script_tool_call(
            "execute_python",
            {
                "code": "import os\nprint(os.getcwd())",
                "comment": "Open the project with Python",
            },
            tool_call_id="python-first-1",
        ),
        script_tool_call(
            "exec_command",
            {"cmd": "pwd", "comment": "And the shell, for comparison"},
            tool_call_id="shell-after-1",
        ),
        script_text("Project directory reported."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "On a project",
            "metadata": {
                "mock_llm_script": script,
                "repo": {"owner": owner, "repo": repo},
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    # The repo derives the directory: one source of truth, not two to keep in
    # step.
    recorded_cwd = conversation.json()["metadata"]["cwd"]
    assert recorded_cwd == f"/workspace/repos/{owner}/{repo}", conversation.json()

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Start work on the project.",
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

    python_stdout = returns["python-first-1"]["stdout"] or ""
    # Said, not left to be inferred -- and said on the Python result, which is
    # where this conversation's first tool call actually was.
    assert "[workspace notice]" in python_stdout, returns["python-first-1"]
    assert f"{owner}/{repo}" in python_stdout, returns["python-first-1"]
    # And still the conversation's own directory, the one the shell reports.
    assert recorded_cwd in python_stdout, returns["python-first-1"]
    assert (returns["shell-after-1"]["stdout"] or "").strip().endswith(recorded_cwd), (
        returns["shell-after-1"]
    )


@pytest.mark.asyncio
async def test_scripted_write_todos_normalizes_malformed_and_duplicate_checkbox_input(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """`write_todos`'s recovery paths, driven end to end.

    ``_remove_duplicate_checkbox_prefix`` and the flattened-XML-plan handling in
    ``_split_todo_fragments``/``_parse_todo_lines`` are thoroughly unit-tested in
    ``test_capabilities.py``, but only a well-formed call was ever scripted
    through a real conversation. This drives both recovery paths for real: a
    duplicated leading checkbox, and a flattened plan carrying two items with a
    trailing text status ("- done") in one line.
    """
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"todo_edge_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Exercise the scripted todo edge cases.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["TODO"],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    script = [
        script_tool_call(
            "write_todos",
            {"todos": ["[ ] [x] Ship the release notes"]},
            tool_call_id="todo-duplicate-checkbox-1",
        ),
        script_tool_call(
            "write_todos",
            {
                "todos": [
                    "<todos><item>Draft the proposal</item>"
                    "<item>Send the invoice - done</item></todos>"
                ]
            },
            tool_call_id="todo-flattened-plan-1",
        ),
        script_text("Todo edge cases completed."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Todo edge cases",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Run the scripted todo edge cases.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]
    tool_returns_by_id = {
        item["tool_call_id"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }

    duplicate_checkbox = tool_returns_by_id["todo-duplicate-checkbox-1"]["tool_result"]
    assert duplicate_checkbox["success"] is True
    # The outer, erroneous "[ ]" is dropped; the inner "[x]" wins.
    assert duplicate_checkbox["todos"] == ["- [x] Ship the release notes"]

    flattened_plan = tool_returns_by_id["todo-flattened-plan-1"]["tool_result"]
    assert flattened_plan["success"] is True
    # A multi-item call is a full snapshot: the earlier duplicate-checkbox task
    # is gone and both flattened items are present, one recovered as done from
    # its trailing "- done" text.
    assert flattened_plan["todos"] == [
        "- [ ] Draft the proposal",
        "- [x] Send the invoice",
    ]

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    assert persisted.json()["metadata"]["todos"] == [
        {"content": "Draft the proposal", "done": False},
        {"content": "Send the invoice", "done": True},
    ]


@pytest.mark.asyncio
async def test_a_plan_is_ticked_off_and_the_tool_says_what_is_next(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """The half of the task list that was never happening, end to end.

    Agents wrote the plan and then left it: every item unchecked for the rest of
    the conversation, which is what the person watching reads as "still on step
    one". Two things had to change for that, and both are asserted here -- the
    tool result now names the next item and when to flip it, and a check-off in
    slightly different words lands on the planned task instead of appending a
    near-duplicate beside it.
    """
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"todo_flow_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Plan, then work the plan.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["TODO"],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    script = [
        script_tool_call(
            "write_todos",
            {"todos": ["- [ ] Fetch the Q3 report", "- [ ] Summarize findings"]},
            tool_call_id="todo-plan-1",
        ),
        script_tool_call(
            "write_todos",
            # Reworded on purpose: "the" dropped, which is exactly how a model
            # restates its own task, and exactly what used to append a second
            # completed item while the planned one stayed open.
            {"todos": ["- [x] Fetch Q3 report"]},
            tool_call_id="todo-flip-1",
        ),
        script_text("First step done."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Working the plan",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Research Q3 and summarize it.",
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

    plan = returns["todo-plan-1"]
    assert plan["next"] == "Fetch the Q3 report"
    assert "0 of 2 done" in plan["reminder"]
    # The literal call to make next, so flipping it is copying rather than
    # remembering.
    assert "- [x] Fetch the Q3 report" in plan["reminder"]

    flip = returns["todo-flip-1"]
    assert flip["todos"] == [
        "- [x] Fetch the Q3 report",
        "- [ ] Summarize findings",
    ]
    assert flip["next"] == "Summarize findings"
    assert "1 of 2 done" in flip["reminder"]

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    # Stored under the wording the person already saw, with one item done --
    # not two tasks that mean the same thing.
    assert persisted.json()["metadata"]["todos"] == [
        {"content": "Fetch the Q3 report", "done": True},
        {"content": "Summarize findings", "done": False},
    ]


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_retried_before_the_run_completes(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """A real model/tool loop can recover from a schema-invalid first call."""
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
        name_prefix="tool_retry",
        toolsets=["TODO"],
    )
    conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "metadata": {
                "mock_llm_script": [
                    script_tool_call(
                        "write_todos",
                        {"todos": "this is not a list"},
                        tool_call_id="todo-invalid",
                    ),
                    script_tool_call(
                        "write_todos",
                        {"todos": ["- [x] Recovered after validation feedback"]},
                        tool_call_id="todo-corrected",
                    ),
                    script_text("The invalid call was corrected."),
                ]
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text

    events = await _send_message(
        authenticated_client,
        pod["id"],
        conversation.json()["id"],
        "Write the recovery todo.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation.json()['id']}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in messages.json()["items"]
        if item["kind"] == "TOOL_RETURN"
    }
    assert returns["todo-corrected"]["success"] is True
    assert returns["todo-corrected"]["todos"] == [
        "- [x] Recovered after validation feedback"
    ]


@pytest.mark.asyncio
async def test_scripted_subagent_spawn_await_and_query_are_real_child_runs(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """The SUBAGENTS toolset creates and waits for an actual child conversation."""
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
        name_prefix="subagent_parent",
        toolsets=["SUBAGENTS"],
    )
    script = [
        script_tool_call(
            "spawn_subagent",
            {"input": "Reply with exactly DELTA99."},
            tool_call_id="spawn-1",
        ),
        script_tool_call(
            "interact_subagent",
            {
                "conversation_id": script_tool_result_ref("spawn-1", "conversation_id"),
                "action": "await",
                "run_id": script_tool_result_ref("spawn-1", "run_id"),
                "timeout_seconds": 30,
            },
            tool_call_id="await-1",
        ),
        script_tool_call(
            "query_subagents",
            {"mode": "list"},
            tool_call_id="query-1",
        ),
        script_text("The child completed with DELTA99."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={
            "agent_name": agent["name"],
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod["id"],
        conversation_id,
        "Delegate the DELTA99 task and wait for the child.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in messages.json()["items"]
        if item["kind"] == "TOOL_RETURN"
    }
    assert returns["spawn-1"]["success"] is True
    assert returns["await-1"]["success"] is True
    assert "DELTA99" in str(returns["await-1"])
    assert returns["query-1"]["success"] is True

    children = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations",
        params={"parent_id": conversation_id},
    )
    assert children.status_code == status.HTTP_200_OK, children.text
    assert len(children.json()["items"]) == 1
    assert children.json()["items"][0]["parent_id"] == conversation_id


@pytest.mark.asyncio
async def test_structured_output_finalizes_through_the_real_harness(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """An output-schema agent completes with a persisted structured answer.

    The mock model answers an output-schema agent by calling the output tool
    with minimal valid arguments, so this drives the real final-output branch
    in the in-process harness (`_final_output_message`) and the structured
    serialization of the answer, end to end through the worker.
    """
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    output_schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    agent = await _create_mock_agent(
        authenticated_client,
        pod_id=pod["id"],
        runtime_profile_id=runtime["id"],
        name_prefix="structured",
        output_schema=output_schema,
    )
    conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={"agent_name": agent["name"], "title": "Structured output"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod["id"],
        conversation_id,
        "Answer with the required schema.",
    )
    assert events[-1]["type"] == "completed", events

    messages = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    final = [
        item
        for item in messages.json()["items"]
        if item["metadata"].get("is_final_answer")
    ]
    assert final, "the structured answer was not persisted as a final message"
    assert "structured_output" in final[0]["metadata"]
    assert final[0]["metadata"]["structured_output"] == {"answer": ""}

    conversation_detail = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}"
    )
    assert conversation_detail.status_code == status.HTTP_200_OK, (
        conversation_detail.text
    )
    assert conversation_detail.json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_pod_skill_catalog_discovers_custom_skills_and_skips_malformed_ones(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Skills stored directly in a pod's real `/skills` folder, not just the
    system-shipped ones every pod also sees through the read-only overlay.

    `_build_pod_skill_catalog` walks the pod's real file tree over HTTP-driven
    datastore state. A folder with no `SKILL.md` in it (an interrupted upload,
    or an unrelated folder someone dropped in `/skills`) must be skipped
    rather than failing the whole catalog, and a skill's nested resource files
    must be discoverable through the real recursive listing.
    """
    from app.modules.agent.tools.skills.skill_loader import (
        list_workspace_skill_resources,
        list_workspace_skills,
        read_workspace_skill,
        read_workspace_skill_resource,
    )

    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = UUID(pod["id"])
    user_id = UUID(fixed_test_user["id"])
    api = DatastoreApi(authenticated_client, pod["id"])

    suffix = uuid4().hex[:8]
    custom_name = f"custom-skill-{suffix}"
    await api.create_folder(f"/skills/{custom_name}")
    await api.upload_file(
        "SKILL.md",
        (
            f"---\nname: {custom_name}\n"
            "description: A pod-hosted custom skill for the e2e catalog test.\n"
            "---\n# Custom skill body\n"
        ).encode("utf-8"),
        directory_path=f"/skills/{custom_name}",
        search_enabled=False,
    )
    await api.create_folder(f"/skills/{custom_name}/references")
    await api.upload_file(
        "note.md",
        b"# Nested resource\nFound by the recursive resource walk.",
        directory_path=f"/skills/{custom_name}/references",
        search_enabled=False,
    )

    malformed_name = f"malformed-skill-{suffix}"
    # A skill directory with no SKILL.md dropped inside: the catalog must
    # silently skip it, not raise and take every other skill down with it.
    await api.create_folder(f"/skills/{malformed_name}")

    # Each of these folders has a SKILL.md whose frontmatter is broken in a
    # different way. `_parse_frontmatter` raises on each, and the catalog
    # silently skips the folder rather than failing the whole pod.
    async def _seed_malformed(folder: str, body: bytes) -> None:
        await api.create_folder(f"/skills/{folder}")
        await api.upload_file(
            "SKILL.md", body, directory_path=f"/skills/{folder}", search_enabled=False
        )

    valid_skill_name = f"valid-skill-{suffix}"
    await _seed_malformed(f"no-frontmatter-{suffix}", b"# no yaml frontmatter here")
    await _seed_malformed(
        f"unclosed-frontmatter-{suffix}",
        b"---\nname: x\n",  # never closed with a second '---'
    )
    await _seed_malformed(
        f"missing-name-{suffix}",
        b"---\ndescription: no name field\n---\n",
    )
    await _seed_malformed(
        f"missing-description-{suffix}",
        b"---\nname: x\n---\n",
    )
    await _seed_malformed(
        f"invalid-name-{suffix}",
        b"---\nname: 'Bad Name!'\ndescription: d\n---\n",
    )
    await _seed_malformed(
        f"name-mismatch-{suffix}",
        (
            b"---\nname: " + valid_skill_name.encode() + b"\ndescription: d\n---\n"
        ),  # directory name != frontmatter name
    )

    catalog = await list_workspace_skills(pod_id=pod_id, user_id=user_id)
    names = {item["name"] for item in catalog}
    assert custom_name in names
    assert malformed_name not in names
    for malformed_folder in (
        f"no-frontmatter-{suffix}",
        f"unclosed-frontmatter-{suffix}",
        f"missing-name-{suffix}",
        f"missing-description-{suffix}",
        f"invalid-name-{suffix}",
        f"name-mismatch-{suffix}",
    ):
        assert malformed_folder not in names, malformed_folder
    # The system-shipped skills are visible through the same overlay.
    assert "browser" in names

    content = await read_workspace_skill(custom_name, pod_id=pod_id, user_id=user_id)
    assert "Custom skill body" in content

    # Reading an unknown skill surfaces the resolver's not-found branch.
    with pytest.raises(ValueError, match="Unknown skill"):
        await read_workspace_skill(
            f"does-not-exist-{suffix}", pod_id=pod_id, user_id=user_id
        )

    resources = await list_workspace_skill_resources(
        custom_name, pod_id=pod_id, user_id=user_id
    )
    resource_paths = {item["path"] for item in resources}
    assert "references/note.md" in resource_paths

    resource_content = await read_workspace_skill_resource(
        custom_name, "references/note.md", pod_id=pod_id, user_id=user_id
    )
    assert "Found by the recursive resource walk" in resource_content


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
async def test_public_runtime_profile_edit_archive_and_restore(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    monkeypatch,
):
    """The full management lifecycle a workspace admin drives from the UI.

    The point that needs proving end to end is the PATCH semantics: a rename
    sends only `name`, and the stored API key must survive it. Anything that
    made `api_key` required would silently blank the credential on every save.
    """
    from app.modules.agent.services.runtime_provider_discovery import DiscoveredModel

    async def discover_openai_models(**_kwargs):
        return [DiscoveredModel("mock-safe-model", supports_vision=True)]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_openai_compatible_models",
        discover_openai_models,
    )

    org_id = fixed_test_org["id"]
    base = f"/organizations/{org_id}/agent-runtime/profiles"
    canary = "CANARY_EDITED_PROFILE_KEY_4d19"
    suffix = uuid4().hex[:8]

    created = await authenticated_client.post(
        base,
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Editable {suffix}",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
            "api_key": canary,
            "default_model_name": "mock-safe-model",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    profile_id = created.json()["id"]

    fetched = await authenticated_client.get(f"{base}/{profile_id}")
    assert fetched.status_code == status.HTTP_200_OK, fetched.text
    assert fetched.json()["has_credentials"] is True
    # A provider profile has no harness behind it, so nothing to report.
    assert fetched.json()["harness"] is None
    assert fetched.json()["availability_status"] is None
    assert canary not in fetched.text

    renamed = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={"source": "OPENAI_COMPATIBLE", "name": f"Renamed {suffix}"},
    )
    assert renamed.status_code == status.HTTP_200_OK, renamed.text
    assert renamed.json()["name"] == f"Renamed {suffix}"
    # The key was never in the request; it must still be there.
    assert renamed.json()["has_credentials"] is True
    assert canary not in renamed.text

    other = await authenticated_client.post(
        base,
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Occupied {suffix}",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
            "api_key": "second-key",
            "default_model_name": "mock-safe-model",
        },
    )
    assert other.status_code == status.HTTP_201_CREATED, other.text

    collision = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={"source": "OPENAI_COMPATIBLE", "name": f"Occupied {suffix}"},
    )
    assert collision.status_code == status.HTTP_409_CONFLICT, collision.text

    archived = await authenticated_client.delete(f"{base}/{profile_id}")
    assert archived.status_code == status.HTTP_204_NO_CONTENT, archived.text

    listed = await authenticated_client.get(base)
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert profile_id not in {item["id"] for item in listed.json()["items"]}

    with_archived = await authenticated_client.get(
        base, params={"include_disabled": True}
    )
    assert with_archived.status_code == status.HTTP_200_OK, with_archived.text
    archived_item = next(
        item for item in with_archived.json()["items"] if item["id"] == profile_id
    )
    assert archived_item["status"] == "DISABLED"

    restored = await authenticated_client.post(f"{base}/{profile_id}:restore")
    assert restored.status_code == status.HTTP_200_OK, restored.text
    assert restored.json()["status"] == "ACTIVE"
    # Archiving is reversible without re-entering the credential.
    assert restored.json()["has_credentials"] is True

    back = await authenticated_client.get(base)
    assert profile_id in {item["id"] for item in back.json()["items"]}


@pytest.mark.asyncio
async def test_public_runtime_profile_update_rediscovers_and_clears_credentials(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    monkeypatch,
):
    """`_update_provider` branches the rename-only edit above never reaches.

    A rotated `base_url` re-validates against the SSRF guard and forces
    rediscovery, a dropped default model is followed to whatever the
    rediscovered catalog now offers, and an explicit null `api_key` clears
    credentials on an OPENAI_COMPATIBLE profile but is rejected outright on an
    ANTHROPIC_COMPATIBLE one, which always requires a key.
    """
    from app.modules.agent.services.runtime_provider_discovery import DiscoveredModel

    discovery_calls: list[str] = []

    async def discover_initial(*, base_url: str, **_kwargs):
        discovery_calls.append(base_url)
        return [DiscoveredModel("mock-safe-model", supports_vision=False)]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_openai_compatible_models",
        discover_initial,
    )

    org_id = fixed_test_org["id"]
    base = f"/organizations/{org_id}/agent-runtime/profiles"
    canary = "CANARY_UPDATE_PROVIDER_KEY_71bd"
    suffix = uuid4().hex[:8]

    created = await authenticated_client.post(
        base,
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Update matrix {suffix}",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
            "api_key": canary,
            "default_model_name": "mock-safe-model",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    profile_id = created.json()["id"]

    async def discover_after_move(*, base_url: str, **_kwargs):
        discovery_calls.append(base_url)
        return [DiscoveredModel("moved-model", supports_vision=False)]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_openai_compatible_models",
        discover_after_move,
    )
    moved = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={
            "source": "OPENAI_COMPATIBLE",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v2",
        },
    )
    assert moved.status_code == status.HTTP_200_OK, moved.text
    # The old default fell out of the rediscovered catalog, so the edit
    # followed it to whatever the provider offers now instead of failing.
    assert moved.json()["default_model_name"] == "moved-model"
    assert {m["name"] for m in moved.json()["model_catalog"]} == {"moved-model"}
    assert f"{_UNUSED_MODEL_BASE_URL}/v2" in discovery_calls
    assert canary not in moved.text

    unsafe_move = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={
            "source": "OPENAI_COMPATIBLE",
            "base_url": "http://169.254.169.254/latest",
        },
    )
    assert unsafe_move.status_code == status.HTTP_400_BAD_REQUEST
    assert unsafe_move.json()["message"] == "base_url must be a public http(s) URL"

    cleared = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={"source": "OPENAI_COMPATIBLE", "api_key": None},
    )
    assert cleared.status_code == status.HTTP_200_OK, cleared.text
    assert cleared.json()["has_credentials"] is False

    async def discover_anthropic(**_kwargs):
        return [DiscoveredModel("mock-safe-model", supports_vision=True)]

    monkeypatch.setattr(
        "app.modules.agent.services.runtime_provider_discovery."
        "_discover_anthropic_compatible_models",
        discover_anthropic,
    )
    anthropic_created = await authenticated_client.post(
        base,
        json={
            "source": "ANTHROPIC_COMPATIBLE",
            "name": f"Anthropic update {suffix}",
            "base_url": f"{_UNUSED_MODEL_BASE_URL}/v1",
            "api_key": "initial-anthropic-key",
            "default_model_name": "mock-safe-model",
        },
    )
    assert anthropic_created.status_code == status.HTTP_201_CREATED, (
        anthropic_created.text
    )
    anthropic_id = anthropic_created.json()["id"]

    # An Anthropic-compatible profile always needs a key: unlike the
    # OPENAI_COMPATIBLE case above, clearing it is rejected rather than
    # allowed through.
    rejected_clear = await authenticated_client.patch(
        f"{base}/{anthropic_id}",
        json={"source": "ANTHROPIC_COMPATIBLE", "api_key": None},
    )
    assert rejected_clear.status_code == status.HTTP_400_BAD_REQUEST
    assert "requires an API key" in rejected_clear.json()["message"]


@pytest.mark.asyncio
async def test_archived_runtime_profile_fails_a_pinned_agent_run_safely(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """A user cannot run an agent against a profile archived meanwhile."""
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
        name_prefix="archived_profile",
    )

    archived = await authenticated_client.delete(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles/{runtime['id']}"
    )
    assert archived.status_code == status.HTTP_204_NO_CONTENT, archived.text

    conversation = await authenticated_client.post(
        f"/pods/{pod['id']}/conversations",
        json={"agent_name": agent["name"], "title": "Archived profile run"},
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]
    events = await _send_message(
        authenticated_client,
        pod["id"],
        conversation_id,
        "Run even though the profile was archived.",
    )
    assert events[-1]["type"] == "error", events
    assert json.dumps(events).find(_RUNTIME_SECRET) == -1

    durable = await authenticated_client.get(
        f"/pods/{pod['id']}/conversations/{conversation_id}"
    )
    assert durable.status_code == status.HTTP_200_OK, durable.text
    assert durable.json()["status"] == "FAILED"


@pytest.mark.asyncio
async def test_public_agent_host_profile_update_touches_and_skips_the_harness(
    authenticated_client,
    async_client,
    fixed_test_org,
    e2e_settings,
    db_session,
):
    """`update_agent_host_profile`, entirely untested before this: driven
    through the real pairing + harness-publish seam other Agent Host suites
    use, not a stand-in for one.

    A rename alone must not need the harness re-validated. A configuration or
    model change must re-validate against the live harness and re-pin the
    config snapshot revision (`harness_snapshot_revision`) -- the value a
    dispatch checks to refuse a profile saved against a harness that has since
    changed underneath it. An unknown selection is rejected outright, and
    `host_wait_timeout_seconds` alone takes the cheap branch that never
    touches the harness at all.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from sqlalchemy import update

    from app.modules.agent.infrastructure.runtime_models import AgentHostModel
    from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine

    display_name = f"editor-e2e-{uuid4().hex[:8]}"
    machine = await paired_machine(
        SimpleNamespace(owner_client=authenticated_client, async_client=async_client),
        display_name=display_name,
        config_options=[
            {
                "id": "model",
                "name": "Model",
                "category": "model",
                "options": [
                    {"name": "GPT-5 Codex", "value": "gpt-5-codex"},
                    {"name": "GPT-5 Codex Mini", "value": "gpt-5-codex-mini"},
                ],
            },
            {
                "id": "reasoning_effort",
                "name": "Reasoning Effort",
                "category": "reasoning_effort",
                "options": [
                    {"name": "Low", "value": "low"},
                    {"name": "High", "value": "high"},
                ],
            },
        ],
    )
    harness_id = str(machine["harness_id"])

    # A paired host only accepts new runs while its heartbeat is fresh, and the
    # heartbeat rides on the 25s long poll -- which a test cannot sit through.
    # Stamping it is the same thing that poll does, without the wait (same
    # pattern as test_agent_host_vision_e2e.py's _profile_for_a_host_that,
    # which reads it back through the same session rather than a separate
    # HTTP request -- this test goes through authenticated_client, a
    # different connection, so it needs a real commit, not just a flush).
    await db_session.execute(
        update(AgentHostModel)
        .where(AgentHostModel.id == machine["host_id"])
        .values(status="ONLINE", last_seen_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    org_id = fixed_test_org["id"]
    base = f"/organizations/{org_id}/agent-runtime/profiles"
    created = await authenticated_client.post(
        base,
        json={
            "source": "AGENT_HOST",
            "harness_id": harness_id,
            "name": f"Laptop {display_name}",
            "default_model_name": "gpt-5-codex",
            "config_selections": {"reasoning_effort": "low"},
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    profile_id = created.json()["id"]

    # A rename does not touch the harness config at all.
    renamed = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={"source": "AGENT_HOST", "name": f"Renamed {display_name}"},
    )
    assert renamed.status_code == status.HTTP_200_OK, renamed.text
    assert renamed.json()["name"] == f"Renamed {display_name}"
    assert renamed.json()["default_model_name"] == "gpt-5-codex"

    # A configuration change re-validates against the live harness. The
    # pinned model is untouched by this call and still offered, so it must
    # survive rather than being cleared.
    reconfigured = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={
            "source": "AGENT_HOST",
            "config_selections": {"reasoning_effort": "high"},
        },
    )
    assert reconfigured.status_code == status.HTTP_200_OK, reconfigured.text
    assert reconfigured.json()["default_model_name"] == "gpt-5-codex"

    # An unknown selection is rejected outright.
    invalid_selection = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={
            "source": "AGENT_HOST",
            "config_selections": {"not_a_real_option": "x"},
        },
    )
    assert invalid_selection.status_code == status.HTTP_400_BAD_REQUEST
    assert "Unknown Agent Host configuration" in invalid_selection.json()["message"]

    # host_wait_timeout_seconds alone takes the cheap branch: no harness
    # round trip, and the pinned model is untouched.
    timeout_only = await authenticated_client.patch(
        f"{base}/{profile_id}",
        json={"source": "AGENT_HOST", "host_wait_timeout_seconds": 120},
    )
    assert timeout_only.status_code == status.HTTP_200_OK, timeout_only.text
    assert timeout_only.json()["default_model_name"] == "gpt-5-codex"


@pytest.mark.asyncio
async def test_a_dropped_model_stream_does_not_end_the_conversation(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """The production failure, driven end to end through the public API.

    ~20 runs a week died to an `httpx.ReadError` raised while iterating the
    provider's SSE stream: the request was accepted, the connection dropped
    mid-answer, and the whole conversation run failed. The harness now resumes
    from the messages already recorded — so the caller sees one clean answer,
    not an error, and not the abandoned half-response glued onto the retry.
    """
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client, fixed_test_org, e2e_settings
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"streamdrop_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Reply using the scripted deterministic model.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "metadata": {
                "mock_llm_script": [
                    {
                        "text": "The complete answer.",
                        "tool_calls": [],
                        # Fails the first attempt only; the retry must get through.
                        "error": {"kind": "stream_drop", "times": 1},
                    }
                ],
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Answer despite a bad connection.",
    )

    assert events[-1]["type"] == "completed", events
    assert not [event for event in events if event["type"] == "error"], events

    # The client was told to discard whatever it had streamed before the drop.
    assert any(
        event["type"] == "token" and event.get("kind") == "stream_reset"
        for event in events
    ), events

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    assistant_texts = [
        item["text"]
        for item in messages.json()["items"]
        if item["role"] == "assistant" and item["kind"] == "TEXT"
    ]
    # Exactly one answer persisted: the abandoned attempt left nothing behind.
    assert assistant_texts == ["The complete answer."], assistant_texts


@pytest.mark.asyncio
async def test_a_model_stream_that_keeps_dropping_fails_cleanly_once_retries_run_out(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    """The retry above has a ceiling: `agent_model_stream_max_attempts` (3).

    A connection that drops on *every* attempt must not hang the run or retry
    forever — it has to give up and report the sanitized "kept dropping"
    message, the one branch of `_user_facing_error_message` that dropping only
    once (the test above) never reaches.
    """
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client, fixed_test_org, e2e_settings
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"streamdrop_exhausted_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Reply using the scripted deterministic model.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "metadata": {
                "mock_llm_script": [
                    {
                        "text": "Never delivered.",
                        "tool_calls": [],
                        # Drops far more times than the retry ceiling allows,
                        # so every attempt fails and the run has to give up.
                        "error": {"kind": "stream_drop", "times": 50},
                    }
                ],
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Answer despite a connection that never recovers.",
    )

    assert events[-1]["type"] == "error", events
    assert "kept dropping" in str(events[-1]["data"])

    durable = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert durable.status_code == status.HTTP_200_OK, durable.text
    assert durable.json()["status"] == "FAILED"


async def test_scripted_messaging_toolset_completes_the_ask_and_answer_loop(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    e2e_settings,
    worker,
):
    """The whole MESSAGING toolset, in one run, against real notification rows.

    Three tools that had no e2e coverage anywhere -- not a thin patch each, but
    the sequence they exist to form, because that is where they can disagree:

        list_pod_members -> who can I reach
        message_user     -> reach them, and get a notification id back
        check_messages   -> that id is a real row, and it is OPEN

    The id round-trip is the assertion that matters. Each tool writing its own
    row is provable in isolation; that the id `message_user` hands back is one
    `check_messages` can actually find is not, and that is the join a caller
    depends on.

    Answering is the other half of this loop and lives in
    `test_notification_response_e2e.py`: `respond_to_notification` is not part
    of this toolset at all. It is injected by `OpenNotificationsCapability`,
    only into the conversation a notification was delivered to -- so it cannot
    be reached from the asking side, which is where this run stands.
    """
    runtime = await _create_runtime_profile(
        authenticated_client, fixed_test_org, e2e_settings
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"messaging_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Run the scripted messaging steps.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": ["MESSAGING"],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    notification_id = script_tool_result_ref("msg-1", "notification_id")
    script = [
        script_tool_call("list_pod_members", {}, tool_call_id="members-1"),
        script_tool_call(
            "message_user",
            {
                "to": fixed_test_user["email"],
                "message": "Can you confirm the Q3 numbers?",
                "background_instruction": "Record whatever they say.",
            },
            tool_call_id="msg-1",
        ),
        script_tool_call(
            "check_messages",
            {"notification_ids": [notification_id]},
            tool_call_id="check-open",
        ),
        script_text("Asked; waiting on an answer."),
    ]

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Messaging loop",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client, pod_id, conversation_id, "Ask about Q3 and record it."
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

    assert returns["members-1"]["success"] is True
    assert returns["members-1"]["members"], "the run's own owner is a pod member"

    sent = returns["msg-1"]
    assert sent["success"] is True, sent
    assert sent["notification_id"], "no id came back, so nothing can check it"

    still_open = returns["check-open"]
    assert still_open["success"] is True, still_open
    assert still_open["pending"] == 1
    assert [m["status"] for m in still_open["messages"]] == ["OPEN"]

    assert str(sent["notification_id"]) in str(still_open["messages"][0]), (
        "check_messages found a row, but not the one message_user reported"
    )
