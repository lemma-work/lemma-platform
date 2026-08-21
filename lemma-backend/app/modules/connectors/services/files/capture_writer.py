"""Decide what a caller actually receives when an operation returns a file.

Size decides, not whether the caller remembered to pass ``output_path``:

* under the inline ceiling -- returned as base64, so a small CSV needs no
  datastore round trip and no second fetch;
* over it -- streamed to the pod datastore and returned as a ``pod_file``
  reference, so a large download is never held in memory twice (raw, then
  base64) nor serialized whole into a JSON response;
* over the hard ceiling -- refused, rather than buffered and then refused.

``output_path`` now only chooses *where* a persisted file lands.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.connectors.config import connector_settings
from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.core.concurrency.offload import run_blocking
from app.modules.connectors.services.files.capture import (
    BinaryCandidate,
    find_binary,
    replace_at,
)

logger = get_logger(__name__)


def _default_directory(connector_id: str) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"/me/connector-downloads/{connector_id}/{day}"


def _inline(candidate: BinaryCandidate, data: bytes) -> dict[str, Any]:
    return {
        "type": "binary_content",
        "content_base64": base64.b64encode(data).decode(),
        "media_type": candidate.media_type or "application/octet-stream",
        "file_name": candidate.filename,
        "size_bytes": len(data),
    }


async def _fetch(candidate: BinaryCandidate) -> bytes:
    """Download a URL-backed candidate, guarded and capped."""
    from app.core.net.http_client import get_shared_http_client
    from app.core.net.url_guard import UnsafeUrlError, fetch_guarded

    try:
        return await fetch_guarded(
            get_shared_http_client(),
            candidate.url or "",
            max_bytes=connector_settings.connector_response_max_bytes,
            timeout=60.0,
        )
    except UnsafeUrlError as exc:
        raise OperationExecutionValidationError(
            "Refused to download the operation's file output.",
            details={"reason": exc.reason},
        ) from exc


class BinaryResultWriter:
    """Turns a binary operation result into something the caller can use."""

    def __init__(self, pod_file_gateway: Any | None):
        self._gateway = pod_file_gateway

    async def resolve(self, result: Any) -> tuple[BinaryCandidate, bytes] | None:
        """Find the binary and get its bytes. Touches no database.

        Deliberately separate from persisting it: this walks and base64-decodes
        the whole third-party response, and for a URL-sourced result it also
        downloads the file. Doing that inside the session that persists it held
        a pooled connection across both.
        """
        candidate = await run_blocking(find_binary, result, limiter="cpu_bound")
        if candidate is None:
            return None

        data = (
            candidate.data if candidate.source == "inline" else await _fetch(candidate)
        )
        if data is None:
            return None

        if len(data) > connector_settings.connector_response_max_bytes:
            raise OperationExecutionValidationError(
                "Operation returned a file larger than the allowed limit.",
                details={"reason": "response_too_large"},
            )
        return candidate, data

    async def capture(
        self,
        result: Any,
        *,
        connector_id: str,
        pod_id: UUID | None,
        ctx: Any | None,
        output_path: str | None = None,
        resolved: tuple[BinaryCandidate, bytes] | None = None,
    ) -> Any:
        """Persist the binary in ``result``. Needs a database session.

        Call :meth:`resolve` first and pass its answer in when the caller wants
        the expensive half to happen outside a session — which is the whole
        reason the two are separable.
        """
        resolved = resolved or await self.resolve(result)
        if resolved is None:
            return result
        candidate, data = resolved

        wants_persist = (
            output_path is not None
            or len(data) > connector_settings.connector_inline_result_max_bytes
        )
        if not wants_persist or self._gateway is None or pod_id is None:
            return replace_at(result, candidate.path, _inline(candidate, data))

        directory, name = self._destination(candidate, connector_id, output_path)
        written = await self._gateway.write_bytes(
            pod_id=pod_id,
            directory=directory,
            name=name,
            content=data,
            media_type=candidate.media_type,
            ctx=ctx,
        )
        return replace_at(result, candidate.path, written)

    @staticmethod
    def _destination(
        candidate: BinaryCandidate, connector_id: str, output_path: str | None
    ) -> tuple[str, str]:
        if output_path:
            trimmed = output_path.rstrip("/")
            if "/" in trimmed:
                directory, _, name = trimmed.rpartition("/")
                return (directory or "/"), (name or candidate.filename or "download")
            return "/", trimmed
        return (
            _default_directory(connector_id),
            candidate.filename or "download",
        )
