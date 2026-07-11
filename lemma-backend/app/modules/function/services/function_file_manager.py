"""Function code storage adapters."""

from __future__ import annotations

from datetime import datetime
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

    async def write_file(self, path: str, content: bytes | str):
        if isinstance(content, str):
            content = content.encode("utf-8")

        await obs.put_async(self.store, path, content)
        return {
            "name": path.split("/")[-1],
            "path": path,
            "size": len(content),
            "last_modified": datetime.now().isoformat(),
        }
