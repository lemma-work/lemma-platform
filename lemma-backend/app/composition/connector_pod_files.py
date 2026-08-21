"""Pod datastore file access for connector operations.

Wraps the datastore file service so connectors can resolve a pod path such as
``/me/report.pdf`` to bytes for an upload, and land a downloaded file back in the
pod. It lives in ``composition`` rather than inside the connectors module because
it is the one place the two modules meet: connectors depends only on
``PodFileGatewayPort``, and this is the adapter that satisfies it.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple
from uuid import UUID

from app.modules.connectors.domain.ports import PodFileGatewayPort


class DatastorePodFileGateway(PodFileGatewayPort):
    def __init__(self, uow: Any):
        # Import lazily to avoid a connectors -> datastore import cycle at module load.
        from app.modules.datastore.api.dependencies import build_file_service

        self._service = build_file_service(uow)

    async def read_bytes(
        self, *, pod_id: UUID, path: str, ctx: Any
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        entity, content = await self._service.download_file_content_by_path(
            pod_id, path, ctx
        )
        return (
            content,
            getattr(entity, "mime_type", None),
            getattr(entity, "name", None),
        )

    async def write_bytes(
        self,
        *,
        pod_id: UUID,
        directory: str,
        name: str,
        content: bytes,
        media_type: Optional[str],
        ctx: Any,
    ) -> dict[str, Any]:
        entity = await self._service.create_file(
            pod_id,
            name,
            content,
            ctx,
            directory_path=directory or "/",
        )
        fallback_path = f"{(directory or '/').rstrip('/')}/{name}"
        return {
            "type": "pod_file",
            "pod_path": getattr(entity, "path", fallback_path),
            "size_bytes": getattr(entity, "size_bytes", len(content)),
            "media_type": media_type or getattr(entity, "mime_type", None),
        }
