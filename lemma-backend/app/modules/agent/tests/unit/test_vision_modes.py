"""Image-returning tools must never hand image content to a text-only model.

`view_image` was gated on the model declaring VISION; `pod_view_document_pages`
returned `BinaryContent` to whatever model was running. With
`LEMMA_OPENAI_VISION_MODEL_NAMES` defaulting to empty, the common deployment
therefore withheld the safe tool and offered the unsafe one — a text-only model
asked for a PDF page and the provider rejected the whole request.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic_ai import ToolReturn

from app.modules.agent.domain.vision import AgentVisionMode, resolve_vision_mode
from app.modules.agent.tools.pod import pydantic_adapter as pod_adapter
from app.modules.agent.tools.pod.models import ViewDocumentPagesRequest
from app.modules.agent.tools.context import BaseAgentContext

pytestmark = pytest.mark.unit


class TestModeResolution:
    def test_a_vision_capable_model_reads_images_itself(self) -> None:
        assert (
            resolve_vision_mode(
                model_supports_vision=True, delegate_model_configured=False
            )
            is AgentVisionMode.DIRECT
        )

    def test_a_text_only_model_delegates_when_one_is_configured(self) -> None:
        assert (
            resolve_vision_mode(
                model_supports_vision=False, delegate_model_configured=True
            )
            is AgentVisionMode.DELEGATED
        )

    def test_with_neither_the_agent_simply_cannot_see(self) -> None:
        mode = resolve_vision_mode(
            model_supports_vision=False, delegate_model_configured=False
        )
        assert mode is AgentVisionMode.UNAVAILABLE
        assert not mode.can_see

    def test_a_direct_model_does_not_need_a_delegate(self) -> None:
        """Configuring VISION_MODEL must not divert a model that can already see."""
        assert (
            resolve_vision_mode(
                model_supports_vision=True, delegate_model_configured=True
            )
            is AgentVisionMode.DIRECT
        )

    def test_unavailable_is_the_default_on_a_bare_context(self) -> None:
        """A tool that cannot determine the mode must assume the unsafe case."""
        ctx = BaseAgentContext(
            user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4()
        )
        assert ctx.vision_mode is AgentVisionMode.UNAVAILABLE


def _pdf_services(monkeypatch):
    from app.modules.datastore.services.files.renderer import RenderedPage

    entity = SimpleNamespace(path="/pod/report.pdf", pod_id=uuid4())
    pages = [
        RenderedPage(1, b"jpeg-1", False, "pods/x/report.pdf/page_0001.jpg"),
        RenderedPage(2, b"jpeg-2", True, "pods/x/report.pdf/page_0002.jpg"),
    ]
    services = SimpleNamespace(
        file=SimpleNamespace(
            render_document_page_images=AsyncMock(return_value=(entity, pages)),
            storage=object(),
        ),
        ctx=SimpleNamespace(pod_id=uuid4(), user_id=uuid4()),
    )

    class _Ctx:
        async def __aenter__(self):
            return services

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(pod_adapter, "pod_services", lambda deps: _Ctx())

    async def fake_url(storage, key, expires_seconds=None):
        return f"https://signed/{key}", None

    monkeypatch.setattr(pod_adapter, "build_object_url", fake_url)


def _ctx(mode: AgentVisionMode) -> SimpleNamespace:
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            vision_mode=mode,
        )
    )


class TestPdfPagesRespectTheMode:
    @pytest.mark.asyncio
    async def test_direct_still_receives_the_page_images(self, monkeypatch) -> None:
        _pdf_services(monkeypatch)

        result = await pod_adapter.pod_view_document_pages(
            _ctx(AgentVisionMode.DIRECT),
            ViewDocumentPagesRequest(path="/pod/report.pdf", page_start=1, page_end=2),
        )

        assert isinstance(result, ToolReturn)
        assert [content.data for content in result.content] == [b"jpeg-1", b"jpeg-2"]

    @pytest.mark.asyncio
    async def test_delegated_returns_words_and_never_image_content(
        self, monkeypatch
    ) -> None:
        """The fix for the bug: a text-only model gets a description, not bytes."""
        _pdf_services(monkeypatch)
        seen: dict[str, object] = {}

        async def fake_describe(images, *, instructions, organization_id, user_id):
            seen["labels"] = [image.label for image in images]
            seen["instructions"] = instructions
            return "A flowchart: Ingest -> Validate -> Store."

        monkeypatch.setattr(pod_adapter, "describe_images", fake_describe)

        result = await pod_adapter.pod_view_document_pages(
            _ctx(AgentVisionMode.DELEGATED),
            ViewDocumentPagesRequest(
                path="/pod/report.pdf",
                page_start=1,
                page_end=2,
                instructions="describe the diagram",
            ),
        )

        assert not isinstance(result, ToolReturn), (
            "a ToolReturn carries BinaryContent, which is what breaks the "
            "text-only model"
        )
        assert result["success"] is True
        assert result["viewed_by"] == "vision_model"
        assert "flowchart" in result["descriptions"][0]["description"]
        assert seen["instructions"] == "describe the diagram"
        assert seen["labels"] == [
            "page 1 of /pod/report.pdf",
            "page 2 of /pod/report.pdf",
        ]
        # Page URLs are still returned so the agent can share or re-open them.
        assert len(result["pages"]) == 2

    @pytest.mark.asyncio
    async def test_unavailable_explains_itself_and_points_at_the_text(
        self, monkeypatch
    ) -> None:
        """No vision anywhere: fail in the tool, with a usable alternative —
        not at the provider with an opaque 400."""
        from app.modules.agent.config import agent_settings

        _pdf_services(monkeypatch)
        monkeypatch.delenv("VISION_MODEL", raising=False)
        monkeypatch.setattr(agent_settings, "vision_model", None)

        result = await pod_adapter.pod_view_document_pages(
            _ctx(AgentVisionMode.UNAVAILABLE),
            ViewDocumentPagesRequest(path="/pod/report.pdf", page_start=1),
        )

        assert not isinstance(result, ToolReturn)
        assert result["success"] is False
        assert "VISION_MODEL" in result["error"]
        assert "pod_read_file" in result["error"]


class TestViewImageRespectsTheMode:
    @pytest.mark.asyncio
    async def test_delegated_view_image_returns_a_description(
        self, monkeypatch
    ) -> None:
        from app.modules.agent.tools.workspace_cli import workspace_cli
        from app.modules.agent.tools.workspace_cli.models import ViewImageRequest

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

        async def fake_read(ctx, path):
            return png, "image/png"

        monkeypatch.setattr(workspace_cli, "read_workspace_file_bytes", fake_read)

        async def fake_describe(images, *, instructions, organization_id, user_id):
            return "A bar chart with four bars."

        monkeypatch.setattr(workspace_cli, "describe_images", fake_describe)

        result = await workspace_cli.view_image_internal(
            SimpleNamespace(
                pod_id=uuid4(),
                user_id=uuid4(),
                vision_mode=AgentVisionMode.DELEGATED,
            ),
            ViewImageRequest(
                workspace_file_path="chart.png", instructions="read the bars"
            ),
        )

        assert not isinstance(result, ToolReturn)
        assert result.success is True
        assert "bar chart" in result.message


class TestMcpBridgeCarriesImages:
    def test_image_content_rides_alongside_the_text(self) -> None:
        """Both bridges flattened every result to text, so `view_image` reached
        Codex and Claude Code — both vision-capable — as JSON describing a
        picture they never received."""
        from pydantic_ai import BinaryContent, ToolReturn

        from app.modules.agent.services.mcp_content import image_contents

        result = ToolReturn(
            return_value={"success": True, "file_path": "/a.png"},
            content=[BinaryContent(data=b"\x89PNG-bytes", media_type="image/png")],
        )

        images = image_contents(result)

        assert len(images) == 1
        assert images[0].mimeType == "image/png"
        # MCP carries image bytes base64-encoded.
        import base64

        assert base64.b64decode(images[0].data) == b"\x89PNG-bytes"

    def test_a_plain_result_contributes_no_images(self) -> None:
        from app.modules.agent.services.mcp_content import image_contents

        assert image_contents({"success": True}) == []
        assert image_contents(None) == []

    def test_an_oversized_image_is_dropped_but_the_result_survives(self) -> None:
        """A client that rejects the image would lose the text result with it."""
        from pydantic_ai import BinaryContent, ToolReturn

        from app.modules.agent.services.mcp_content import (
            MAX_MCP_IMAGE_BYTES,
            image_contents,
        )

        huge = ToolReturn(
            return_value={"success": True},
            content=[
                BinaryContent(
                    data=b"x" * (MAX_MCP_IMAGE_BYTES + 1), media_type="image/png"
                )
            ],
        )

        assert image_contents(huge) == []

    def test_non_image_binary_is_not_sent_as_an_image(self) -> None:
        from pydantic_ai import BinaryContent, ToolReturn

        from app.modules.agent.services.mcp_content import image_contents

        audio = ToolReturn(
            return_value={"success": True},
            content=[BinaryContent(data=b"RIFF", media_type="audio/wav")],
        )

        assert image_contents(audio) == []


def test_a_vision_capable_run_is_detected_from_its_stored_snapshot() -> None:
    """The MCP bridge rebuilds context from the persisted snapshot, so the
    snapshot has to carry capabilities or every remote harness looks text-only."""
    from app.modules.agent.domain.vision import vision_mode_from_runtime_profile

    seeing = vision_mode_from_runtime_profile(
        {"model_capabilities": ["TEXT", "TOOLS", "VISION"]}
    )
    assert seeing is AgentVisionMode.DIRECT

    text_only = vision_mode_from_runtime_profile({"model_capabilities": ["TEXT"]})
    assert text_only is not AgentVisionMode.DIRECT
