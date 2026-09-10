"""Connectors' side of :class:`PodFileGatewayPort`, over datastore's file operations.

This was `app/composition/connector_pod_files.py`, on the argument that the
composition root is "the one place the two modules meet". It is not: connectors
declares the port, connectors is the only consumer, and datastore now publishes
the two operations behind it (`datastore/contracts/pod_files.py`). What was left
in the middle was an adapter that reached `datastore.api.dependencies` for a
service and then read three attributes off a `DatastoreFileEntity` through
`getattr(..., None)` -- a caller guessing at another module's schema, with the
guess-failure spelled the same as an honest absence.

The two operations arrive as constructor arguments so this can be exercised
without a database. Patching them on this module instead would install the
double inside the unit under test: the mapping below is the whole of what this
class does, and a stub in front of it certifies nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional, Tuple
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.connectors.domain.ports import PodFileGatewayPort, WrittenPodFile
from app.modules.datastore.contracts.pod_files import (
    PodFileContent,
    StoredPodFile,
    read_pod_file,
    write_pod_file,
)

PodFileReader = Callable[..., Awaitable[PodFileContent]]
PodFileWriter = Callable[..., Awaitable[StoredPodFile]]


class DatastorePodFileGateway(PodFileGatewayPort):
    """Resolves a pod path such as ``/me/report.pdf`` to bytes, and lands one back."""

    def __init__(
        self,
        uow: object,
        *,
        read_file: PodFileReader = read_pod_file,
        write_file: PodFileWriter = write_pod_file,
    ) -> None:
        self._uow = uow
        self._read_file = read_file
        self._write_file = write_file

    async def read_bytes(
        self, *, pod_id: UUID, path: str, ctx: Context
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        file = await self._read_file(self._uow, pod_id=pod_id, path=path, ctx=ctx)
        return file.content, file.media_type, file.name

    async def write_bytes(
        self,
        *,
        pod_id: UUID,
        directory: str,
        name: str,
        content: bytes,
        media_type: Optional[str],
        ctx: Context,
    ) -> WrittenPodFile:
        stored = await self._write_file(
            self._uow,
            pod_id=pod_id,
            directory=directory,
            name=name,
            content=content,
            ctx=ctx,
        )
        return {
            "type": "pod_file",
            "pod_path": stored.pod_path,
            "size_bytes": stored.size_bytes,
            # The caller's own claim about what it downloaded wins: it saw the
            # response's content type, and the pod may have inferred a different
            # one from the file name.
            "media_type": media_type or stored.media_type,
        }
