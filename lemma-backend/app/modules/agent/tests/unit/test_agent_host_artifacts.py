from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.domain.context import AgentContext
from app.modules.agent.infrastructure.harnesses import agent_host_artifacts
from app.modules.agent.infrastructure.harnesses.agent_host_artifacts import (
    PodFileAgentHostArtifactWriter,
)


@pytest.mark.asyncio
async def test_acp_image_is_saved_and_rendered_as_a_pod_file(monkeypatch) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"test-image"
    created: list[dict[str, object]] = []

    class FakeFileService:
        async def create_file(self, pod_id, name, content, ctx, **kwargs):
            created.append(
                {
                    "pod_id": pod_id,
                    "name": name,
                    "content": content,
                    "ctx": ctx,
                    **kwargs,
                }
            )
            return SimpleNamespace(path=f"/me/c/test/agent-output/{name}")

    @asynccontextmanager
    async def uow_factory():
        yield object()

    monkeypatch.setattr(
        agent_host_artifacts,
        "build_file_service",
        lambda _uow: FakeFileService(),
    )
    run_id = uuid4()
    pod_id = uuid4()
    ctx = AgentContext(
        user_id=uuid4(),
        pod_id=pod_id,
        conversation_id=uuid4(),
    )
    result = await PodFileAgentHostArtifactWriter(uow_factory).materialize_event(
        payload={
            "content": {
                "type": "image",
                "data": base64.b64encode(png).decode(),
                "mimeType": "image/png",
            }
        },
        pod_id=pod_id,
        user_context=ctx,
        directory_path="/me/c/test/agent-output",
        agent_run_id=run_id,
        event_sequence=7,
        harness_key="codex",
    )

    expected_name = f"agent-image-{run_id}-7-1.png"
    assert result.warnings == ()
    assert created[0]["name"] == expected_name
    assert created[0]["content"] == png
    assert created[0]["search_enabled"] is False
    assert f"![Generated image](/me/c/test/agent-output/{expected_name})" in (
        result.markdown
    )
    assert f"Generated file: [/me/c/test/agent-output/{expected_name}]" in (
        result.markdown
    )


@pytest.mark.asyncio
async def test_acp_embedded_image_resource_is_supported(monkeypatch) -> None:
    gif = b"GIF89a" + b"test-image"

    class FakeFileService:
        async def create_file(self, _pod_id, name, _content, _ctx, **_kwargs):
            return SimpleNamespace(path=f"/me/out/{name}")

    @asynccontextmanager
    async def uow_factory():
        yield object()

    monkeypatch.setattr(
        agent_host_artifacts,
        "build_file_service",
        lambda _uow: FakeFileService(),
    )
    ctx = AgentContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
    )
    result = await PodFileAgentHostArtifactWriter(uow_factory).materialize_event(
        payload={
            "content": {
                "type": "resource",
                "resource": {
                    "blob": base64.b64encode(gif).decode(),
                    "mimeType": "image/gif",
                    "uri": "file:///generated.gif",
                },
            }
        },
        pod_id=ctx.pod_id,
        user_context=ctx,
        directory_path="/me/out",
        agent_run_id=uuid4(),
        event_sequence=1,
        harness_key="claude-code",
    )

    assert result.warnings == ()
    assert ".gif)" in result.markdown


@pytest.mark.asyncio
async def test_invalid_or_spoofed_acp_image_is_not_persisted(monkeypatch) -> None:
    def must_not_build(_uow):
        raise AssertionError("invalid image must be rejected before storage")

    @asynccontextmanager
    async def uow_factory():
        yield object()

    monkeypatch.setattr(agent_host_artifacts, "build_file_service", must_not_build)
    ctx = AgentContext(
        user_id=uuid4(),
        pod_id=uuid4(),
        conversation_id=uuid4(),
    )
    result = await PodFileAgentHostArtifactWriter(uow_factory).materialize_event(
        payload={
            "content": {
                "type": "image",
                "data": base64.b64encode(b"not actually a png").decode(),
                "mimeType": "image/png",
            }
        },
        pod_id=ctx.pod_id,
        user_context=ctx,
        directory_path="/me/out",
        agent_run_id=uuid4(),
        event_sequence=1,
        harness_key="opencode",
    )

    assert result.markdown == ""
    assert result.warnings == (
        "ACP image bytes do not match declared type image/png.",
    )
