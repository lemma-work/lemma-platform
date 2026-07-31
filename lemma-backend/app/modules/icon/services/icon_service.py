"""Shared icon storage backed by object storage or local disk."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from obstore.store import GCSStore
from pydantic import BaseModel

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

_STORAGE_PATH_PATTERN = re.compile(
    r"\Aicons/([^/]+)/([0-9a-fA-F]{32})(\.[A-Za-z0-9]{1,10})\Z"
)
_CANONICAL_EXTENSIONS = {
    extension: extension
    for extension in (
        ".bmp",
        ".bin",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",  # legacy assets are served only as attachment by the controller
        ".tif",
        ".tiff",
        ".webp",
    )
}


class IconUploadResult(BaseModel):
    """Result for a stored icon asset."""

    icon_url: str
    storage_path: str
    content_type: str


class IconService:
    """Store, resolve, and cleanup uploaded icon assets."""

    _PUBLIC_ROUTE_PREFIX = "/public/icons/"

    def __init__(self, public_base_url: str | None = None):
        self.public_base_url = (public_base_url or settings.api_url).rstrip("/")
        self._local_base: Path | None = None
        self.store: GCSStore | None = None

        if settings.effective_public_storage_backend() == "gcs":
            if not settings.public_bucket_name:
                raise ValueError("GCS public storage requires PUBLIC_BUCKET_NAME")
            self.store = GCSStore(bucket=settings.public_bucket_name)
            return

        root = Path(settings.local_file_storage_root)
        self._local_base = root / "public-icons"
        self._local_base.mkdir(parents=True, exist_ok=True)

    def _normalize_storage_path(self, storage_path: str) -> str:
        match = _STORAGE_PATH_PATTERN.fullmatch(storage_path)
        if match is None:
            raise ValueError("Invalid icon storage path")
        raw_user_id, raw_asset_id, raw_extension = match.groups()
        try:
            user_id = UUID(raw_user_id)
            asset_id = UUID(hex=raw_asset_id)
        except ValueError as exc:
            raise ValueError("Invalid icon storage path") from exc
        extension = _CANONICAL_EXTENSIONS.get(raw_extension.lower())
        if extension is None:
            raise ValueError("Invalid icon storage path")
        return f"icons/{user_id}/{asset_id.hex}{extension}"

    def _local_path(self, storage_path: str) -> Path:
        if not self._local_base:
            raise RuntimeError("Local icon storage is not configured")
        base = self._local_base.resolve()
        candidate = (base / self._normalize_storage_path(storage_path)).resolve()
        if not candidate.is_relative_to(base):
            raise ValueError("Invalid icon storage path")
        return candidate

    def _guess_extension(self, filename: str | None, content_type: str | None) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix:
            return suffix

        guessed = mimetypes.guess_extension(content_type or "")
        if guessed == ".jpe":
            return ".jpg"
        if guessed:
            return guessed
        return ".bin"

    def build_public_url(self, storage_path: str) -> str:
        normalized = self._normalize_storage_path(storage_path)
        return f"{self.public_base_url}{self._PUBLIC_ROUTE_PREFIX}{normalized}"

    def get_managed_storage_path(self, icon_url: str | None) -> str | None:
        if not icon_url:
            return None

        parsed = urlparse(icon_url)
        raw_path = parsed.path or icon_url
        if not raw_path.startswith(self._PUBLIC_ROUTE_PREFIX):
            return None

        candidate = raw_path[len(self._PUBLIC_ROUTE_PREFIX) :]
        try:
            return self._normalize_storage_path(candidate)
        except ValueError:
            logger.debug(
                'icon.icon_service.ignoring_malformed_managed_icon_url.diagnostic'
            )
            return None

    async def upload_icon(
        self,
        *,
        file_content: bytes,
        filename: str | None,
        content_type: str | None,
        user_id: UUID,
    ) -> IconUploadResult:
        extension = self._guess_extension(filename, content_type)
        storage_path = self._normalize_storage_path(
            f"icons/{user_id}/{uuid4().hex}{extension}"
        )

        if self._local_base:
            local_path = self._local_path(storage_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_bytes(file_content)
        else:
            assert self.store is not None
            await self.store.put_async(storage_path, file_content)

        return IconUploadResult(
            icon_url=self.build_public_url(storage_path),
            storage_path=storage_path,
            content_type=content_type or "application/octet-stream",
        )

    async def read_icon(self, storage_path: str) -> bytes:
        normalized = self._normalize_storage_path(storage_path)

        if self._local_base:
            local_path = self._local_path(normalized)
            if not local_path.exists():
                raise FileNotFoundError(normalized)
            return local_path.read_bytes()

        assert self.store is not None
        result = await self.store.get_async(normalized)
        if not result:
            raise FileNotFoundError(normalized)
        data = await result.bytes_async()
        return data.to_bytes()

    async def delete_storage_path(self, storage_path: str) -> None:
        normalized = self._normalize_storage_path(storage_path)

        try:
            if self._local_base:
                local_path = self._local_path(normalized)
                if local_path.exists():
                    local_path.unlink()
                return

            assert self.store is not None
            await self.store.delete_async(normalized)
        except FileNotFoundError:
            return
        except Exception:
            logger.debug('icon.icon_service.delete_icon_asset.diagnostic')

    async def delete_by_url(self, icon_url: str | None) -> None:
        storage_path = self.get_managed_storage_path(icon_url)
        if storage_path is None:
            return
        await self.delete_storage_path(storage_path)
