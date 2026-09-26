"""The image tools, when there is no model at all to look with.

`describe_images` resolves a vision model; on a deployment with nothing set up
that is the 503 `model_not_configured`, and it used to escape the tool and fail
the call instead of telling the agent to read the text.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.domain.errors import DomainError
from app.modules.agent.tools import vision_delegation

pytestmark = pytest.mark.unit

# Resolution is vision_service's, a collaborator of the tools under test.
_RESOLVER = "app.modules.agent.services.vision_service._resolve_vision_model"


class _NoModelResolver:
    """Vision model resolution on a deployment where nothing is set up."""

    async def __call__(self, **_: object) -> object:
        raise DomainError(
            "No AI model is set up yet.",
            code="model_not_configured",
            status_code=503,
        )


@pytest.mark.asyncio
async def test_view_image_reports_no_model_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_RESOLVER, _NoModelResolver())
    ctx = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())

    response = await vision_delegation.describe_single_image(
        ctx,  # type: ignore[arg-type]
        data=b"\x89PNG",
        media_type="image/png",
        file_path="/tmp/picture.png",
        source="workspace",
        instructions=None,
    )

    assert response.success is False
    assert response.error is not None
    assert response.error.startswith("No AI model is set up yet.")


@pytest.mark.asyncio
async def test_document_pages_report_no_model_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_RESOLVER, _NoModelResolver())
    ctx = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())
    page = SimpleNamespace(page_number=1, jpeg_bytes=b"\xff\xd8")

    result = await vision_delegation.describe_document_pages(
        ctx,  # type: ignore[arg-type]
        path="/docs/report.pdf",
        pages=[page],
        page_refs=[{"page": 1}],
        instructions=None,
    )

    assert result["success"] is False
    assert "No AI model is set up yet." in result["error"]
    assert "pod_read_file" in result["error"]


@pytest.mark.asyncio
async def test_any_other_domain_error_still_escapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Archived:
        async def __call__(self, **_: object) -> object:
            raise DomainError("gone", code="runtime_profile_archived", status_code=409)

    monkeypatch.setattr(_RESOLVER, _Archived())
    ctx = SimpleNamespace(organization_id=uuid4(), user_id=uuid4())

    with pytest.raises(DomainError):
        await vision_delegation.describe_single_image(
            ctx,  # type: ignore[arg-type]
            data=b"\x89PNG",
            media_type="image/png",
            file_path="/tmp/picture.png",
            source="workspace",
            instructions=None,
        )
