"""Function code storage adapters."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import obstore as obs
from obstore.exceptions import NotFoundError as ObstoreNotFoundError
from obstore.store import LocalStore, ObjectStore


class FunctionFileManager:
    def __init__(
        self,
        function_id: UUID,
        *,
        root_path: str | Path | None = None,
        store: ObjectStore | None = None,
    ):
        if (root_path is None) == (store is None):
            raise ValueError("Provide exactly one of root_path or store")

        self.function_id = function_id
        self.prefix = f"functions/{function_id}/"
        self._local_base: Path | None = Path(root_path) / self.prefix if root_path else None

        if root_path is not None:
            self.store = LocalStore(prefix=self._local_base, mkdir=True)
        else:
            assert store is not None
            self.store = store

    def _local_path(self, path: str) -> Path:
        if self._local_base is None:
            raise RuntimeError("Local storage is not configured")
        return self._local_base / path

    async def read_file(self, path: str) -> bytes | str:
        try:
            result = await obs.get_async(self.store, path)
        except ObstoreNotFoundError:
            raise FileNotFoundError(f"File {path} not found")
        data = await result.bytes_async()
        bytes_data = data.to_bytes()
        try:
            return bytes_data.decode("utf-8")
        except UnicodeDecodeError:
            return bytes_data

    async def read_bytes(self, path: str) -> bytes:
        """Read a file the caller already knows is binary.

        ``read_file`` above guesses, by attempting a UTF-8 decode of the whole
        buffer and falling back on failure. That is right for a caller that
        might get either, and wrong for one that cannot: fetching a function's
        artifact ran a full decode over a multi-megabyte zip, failed, and the
        caller immediately re-encoded the result back to the bytes it started
        with. Two whole-buffer passes to arrive where it began.
        """
        try:
            result = await obs.get_async(self.store, path)
        except ObstoreNotFoundError:
            raise FileNotFoundError(f"File {path} not found")
        data = await result.bytes_async()
        return data.to_bytes()

    async def write_file(self, path: str, content: bytes | str) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")

        await obs.put_async(self.store, path, content)
