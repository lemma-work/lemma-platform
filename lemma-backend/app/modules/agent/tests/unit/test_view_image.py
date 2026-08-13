from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic_ai import BinaryContent, ToolReturn

from app.core.domain.errors import DomainError
from app.modules.agent.tools.file_access import (
    read_workspace_file_bytes,
    sniff_image_mime,
)
from app.modules.agent.tools.workspace_cli import workspace_cli
from app.modules.agent.tools.workspace_cli.models import (
    ViewImageRequest,
    ViewImageResponse,
)

from app.modules.agent.domain.vision import AgentVisionMode

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _ctx(vision_mode: AgentVisionMode = AgentVisionMode.DIRECT) -> SimpleNamespace:
    return SimpleNamespace(
        pod_id=uuid4(), user_id=uuid4(), vision_mode=vision_mode
    )


def _patch_readers(monkeypatch, *, pod=None, workspace=None) -> None:
    """Replace the per-store readers with stubs that return a tuple or raise."""

    async def _pod(ctx, path):
        if isinstance(pod, Exception):
            raise pod
        return pod

    async def _ws(ctx, path):
        if isinstance(workspace, Exception):
            raise workspace
        return workspace

    monkeypatch.setattr(workspace_cli, "read_pod_file_bytes", _pod)
    monkeypatch.setattr(workspace_cli, "read_workspace_file_bytes", _ws)


# ---- request validation ----------------------------------------------------


def test_request_never_raises_on_construction():
    # The model must never raise: a raising validator is an argument-validation
    # error that bypasses the graceful tool boundary and can abort the run. The
    # "exactly one path" rule is enforced in the tool body instead.
    ViewImageRequest()
    ViewImageRequest(pod_file_path="/me/a.png")
    ViewImageRequest(workspace_file_path="a.png")
    ViewImageRequest(pod_file_path="/me/a.png", workspace_file_path="a.png")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"pod_file_path": "/me/a.png", "workspace_file_path": "a.png"},
        {"pod_file_path": "   "},
    ],
)
async def test_view_image_requires_exactly_one_path(monkeypatch, request_kwargs):
    # Both readers stubbed so a routing bug would surface as success=True.
    _patch_readers(monkeypatch, pod=(_PNG, "image/png"), workspace=(_PNG, "image/png"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(**request_kwargs)
    )

    assert isinstance(result, ViewImageResponse)
    assert result.success is False
    assert "exactly one" in (result.error or "").lower()


# ---- happy paths -----------------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_reads_datastore(monkeypatch):
    _patch_readers(monkeypatch, pod=(_PNG, "image/png"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(pod_file_path="/me/photo.png")
    )

    assert isinstance(result, ToolReturn)
    assert result.return_value.success is True
    assert result.return_value.source == "datastore"
    assert result.return_value.media_type == "image/png"
    assert result.return_value.size_bytes == len(_PNG)
    assert len(result.content) == 1
    assert isinstance(result.content[0], BinaryContent)
    assert result.content[0].media_type == "image/png"


@pytest.mark.asyncio
async def test_view_image_reads_workspace(monkeypatch):
    _patch_readers(monkeypatch, workspace=(_PNG, "image/png"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(workspace_file_path="images/out.png")
    )

    assert isinstance(result, ToolReturn)
    assert result.return_value.source == "workspace"
    assert isinstance(result.content[0], BinaryContent)


# ---- non-image redirect ----------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_rejects_pdf_with_redirect(monkeypatch):
    _patch_readers(monkeypatch, pod=(b"%PDF-1.7", "application/pdf"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(pod_file_path="/me/report.pdf")
    )

    assert isinstance(result, ViewImageResponse)
    assert result.success is False
    assert "pod_view_document_pages" in (result.error or "")


@pytest.mark.asyncio
async def test_view_image_rejects_non_image(monkeypatch):
    _patch_readers(monkeypatch, workspace=(b"hello", "text/plain"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(workspace_file_path="notes.txt")
    )

    assert isinstance(result, ViewImageResponse)
    assert result.success is False


# ---- size guard ------------------------------------------------------------


@pytest.mark.asyncio
async def test_view_image_size_guard(monkeypatch):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (workspace_cli.MAX_VIEW_IMAGE_BYTES + 1)
    _patch_readers(monkeypatch, workspace=(big, "image/png"))

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(workspace_file_path="huge.png")
    )

    assert isinstance(result, ViewImageResponse)
    assert result.success is False
    assert result.size_bytes == len(big)
    assert "limit" in (result.error or "").lower()


# ---- grant -> needs_approval ----------------------------------------------


@pytest.mark.asyncio
async def test_view_image_missing_grant_needs_approval(monkeypatch):
    exc = DomainError(
        "no read grant", code="MISSING_WORKLOAD_RESOURCE_GRANT", status_code=403
    )
    _patch_readers(monkeypatch, pod=exc)

    result = await workspace_cli.view_image_internal(
        _ctx(), ViewImageRequest(pod_file_path="/me/secret.png")
    )

    assert isinstance(result, dict)
    assert result["success"] is False
    assert result["needs_approval"] is True
    assert result["approval"]["tool_name"] == "view_image"


# ---- mime sniff ------------------------------------------------------------


def test_sniff_image_mime():
    assert sniff_image_mime(b"\x89PNG\r\n\x1a\n\x00") == "image/png"
    assert sniff_image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_image_mime(b"GIF89a") == "image/gif"
    assert sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
    assert sniff_image_mime(b"not an image") is None


@pytest.mark.asyncio
async def test_workspace_reader_sniffs_extensionless_png():
    async def _read_file(path):
        return _PNG

    deps = SimpleNamespace(file_manager=SimpleNamespace(read_file=_read_file))

    content, mime = await read_workspace_file_bytes(deps, "screenshot")

    assert mime == "image/png"
    assert content == _PNG
