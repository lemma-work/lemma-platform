from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from app.core.config import settings
from app.modules.icon.services.icon_service import IconService


TEST_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
TEST_ASSET_ID = "a" * 32


@pytest.fixture
def local_icon_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "public_bucket_name", None)
    monkeypatch.setattr(settings, "local_file_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "api_url", "https://api.example.test")
    return tmp_path


@pytest.mark.asyncio
async def test_upload_icon_writes_to_local_storage_and_infers_extension(
    local_icon_settings: Path,
):
    service = IconService()

    result = await service.upload_icon(
        file_content=b"jpeg-bytes",
        filename="avatar",
        content_type="image/jpeg",
        user_id=TEST_USER_ID,
    )

    assert result.content_type == "image/jpeg"
    assert result.storage_path.startswith(f"icons/{TEST_USER_ID}/")
    assert result.storage_path.endswith(".jpg")
    assert (
        result.icon_url
        == f"https://api.example.test/public/icons/{result.storage_path}"
    )
    assert (
        local_icon_settings / "public-icons" / result.storage_path
    ).read_bytes() == b"jpeg-bytes"


@pytest.mark.asyncio
async def test_upload_icon_prefers_filename_suffix_over_content_type(
    local_icon_settings: Path,
):
    service = IconService(public_base_url="https://assets.example.test/")

    result = await service.upload_icon(
        file_content=b"svg-bytes",
        filename="mark.svg",
        content_type="image/png",
        user_id=TEST_USER_ID,
    )

    assert result.storage_path.endswith(".svg")
    assert result.icon_url.startswith("https://assets.example.test/public/icons/")
    assert (
        local_icon_settings / "public-icons" / result.storage_path
    ).read_bytes() == b"svg-bytes"


@pytest.mark.asyncio
async def test_read_and_delete_by_managed_url(local_icon_settings: Path):
    service = IconService()
    storage_path = f"icons/{TEST_USER_ID}/{TEST_ASSET_ID}.png"
    local_path = local_icon_settings / "public-icons" / storage_path
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"png-bytes")

    assert await service.read_icon(storage_path) == b"png-bytes"

    await service.delete_by_url(f"https://api.example.test/public/icons/{storage_path}")

    assert not local_path.exists()


def test_get_managed_storage_path_ignores_unmanaged_or_malformed_urls(
    local_icon_settings: Path,
):
    service = IconService()

    assert (
        service.get_managed_storage_path("https://example.test/assets/icon.png") is None
    )
    assert (
        service.get_managed_storage_path(
            "https://example.test/public/icons/icons/../secret.png"
        )
        is None
    )


@pytest.mark.parametrize(
    "storage_path",
    [
        "",
        ".",
        "icons/../secret.png",
        "icons/./secret.png",
        f"icons/{TEST_USER_ID}/icon.png",
        f"icons/{TEST_USER_ID}/{TEST_ASSET_ID}.exe",
        f"icons\\{TEST_USER_ID}\\{TEST_ASSET_ID}.png",
    ],
)
def test_public_url_rejects_malformed_storage_paths(
    local_icon_settings: Path,
    storage_path: str,
):
    service = IconService()

    with pytest.raises(ValueError, match="Invalid icon storage path"):
        service.build_public_url(storage_path)


@pytest.mark.asyncio
async def test_local_icon_path_rejects_symlink_escape(
    local_icon_settings: Path,
) -> None:
    service = IconService()
    outside = local_icon_settings / "outside"
    outside.mkdir()
    user_directory = local_icon_settings / "public-icons" / "icons" / str(TEST_USER_ID)
    user_directory.parent.mkdir(parents=True)
    user_directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="Invalid icon storage path"):
        await service.read_icon(f"icons/{TEST_USER_ID}/{TEST_ASSET_ID}.png")
