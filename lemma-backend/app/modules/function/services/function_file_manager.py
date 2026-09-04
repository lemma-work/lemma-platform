"""Function code storage adapters."""

from __future__ import annotations

import shutil
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
        self._local_base: Path | None = (
            Path(root_path) / self.prefix if root_path else None
        )

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

    async def delete_file(self, path: str) -> None:
        try:
            await obs.delete_async(self.store, path)
        except ObstoreNotFoundError:
            return

    async def delete_prefix(self, prefix: str) -> None:
        """Delete everything under ``prefix``.

        Function bytes had no deletion path at all, so every deleted function
        orphaned its artifacts permanently and retention had nothing to call.

        Listing is async: the sync ListStream blocks the event loop on each page
        fetch against a cloud store, and retention walks these prefixes often. A
        concurrent sweep can delete the same key first, so a missing object on
        the batch delete is not an error.
        """
        normalized_prefix = prefix.rstrip("/")
        list_prefix = normalized_prefix or None
        async for chunk in self.store.list_async(prefix=list_prefix):
            paths = [
                item["path"]
                for item in chunk
                if isinstance(item, dict) and item.get("path")
            ]
            if not paths:
                continue
            try:
                await self.store.delete_async(paths)
            except ObstoreNotFoundError:
                continue
        if self._local_base:
            target_dir = (
                self._local_base
                if not normalized_prefix
                else self._local_path(normalized_prefix)
            )
            shutil.rmtree(target_dir, ignore_errors=True)
