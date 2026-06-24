from __future__ import annotations

from uuid import UUID

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.core.api.dependencies import get_current_user
from app.core.config import settings
from app.modules.icon.api.controllers.icon_controller import router
from app.modules.identity.domain.user_entities import UserEntity


TEST_USER_ID = UUID("11111111-1111-4111-8111-111111111111")


@pytest.fixture
def icon_app(monkeypatch: pytest.MonkeyPatch, tmp_path) -> FastAPI:
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "public_bucket_name", None)
    monkeypatch.setattr(settings, "local_file_storage_root", str(tmp_path))

    app = FastAPI()
    app.include_router(router)

    def _current_user() -> UserEntity:
        return UserEntity(id=TEST_USER_ID, email="icon-user@example.com")

    app.dependency_overrides[get_current_user] = _current_user
    return app


@pytest.fixture
async def client(icon_app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=icon_app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_upload_icon_persists_file_and_public_endpoint_serves_it(client: AsyncClient):
    image_bytes = b"\x89PNG\r\n\x1a\nicon-bytes"

    upload_response = await client.post(
        "/icons/upload",
        files={"file": ("logo", image_bytes, "image/png")},
    )

    assert upload_response.status_code == status.HTTP_201_CREATED, upload_response.text
    payload = upload_response.json()
    assert payload["content_type"] == "image/png"
    assert payload["storage_path"].startswith(f"icons/{TEST_USER_ID}/")
    assert payload["storage_path"].endswith(".png")
    assert payload["icon_url"] == (
        f"http://testserver/public/icons/{payload['storage_path']}"
    )

    public_response = await client.get(f"/public/icons/{payload['storage_path']}")

    assert public_response.status_code == status.HTTP_200_OK, public_response.text
    assert public_response.content == image_bytes
    assert public_response.headers["content-type"] == "image/png"
    # Serving hardening: nosniff so the browser can't MIME-confuse the bytes.
    assert public_response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_upload_icon_rejects_non_image_upload(client: AsyncClient):
    response = await client.post(
        "/icons/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Only PNG, JPEG, GIF, WEBP, or BMP icons are supported"


@pytest.mark.asyncio
async def test_upload_icon_rejects_svg(client: AsyncClient):
    """SVG can carry <script> and would execute inline on the API origin — it
    must be rejected regardless of the client-supplied content-type
    (security_appsec-03)."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    response = await client.post(
        "/icons/upload",
        files={"file": ("logo.svg", svg, "image/svg+xml")},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_upload_icon_ignores_client_filename_extension(client: AsyncClient):
    """Real PNG bytes sent with a .svg filename are stored as .png — the stored
    extension comes from the verified bytes, never the untrusted filename."""
    png = b"\x89PNG\r\n\x1a\nicon-bytes"
    response = await client.post(
        "/icons/upload",
        files={"file": ("logo.svg", png, "image/svg+xml")},
    )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    assert payload["storage_path"].endswith(".png")
    assert payload["content_type"] == "image/png"


@pytest.mark.asyncio
async def test_upload_icon_rejects_empty_image_upload(client: AsyncClient):
    response = await client.post(
        "/icons/upload",
        files={"file": ("empty.png", b"", "image/png")},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Uploaded icon is empty"


@pytest.mark.asyncio
async def test_get_public_icon_returns_404_for_missing_icon(client: AsyncClient):
    response = await client.get("/public/icons/icons/missing.png")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Icon not found"


@pytest.mark.asyncio
async def test_get_public_icon_rejects_malformed_storage_path(client: AsyncClient):
    response = await client.get("/public/icons/icons/%2E%2E/secret.png")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid icon path"
