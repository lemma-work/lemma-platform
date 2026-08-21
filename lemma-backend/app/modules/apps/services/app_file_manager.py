"""App file manager backed by object storage."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from uuid import UUID

import obstore as obs
from obstore.exceptions import NotFoundError as ObstoreNotFoundError
from obstore.store import LocalStore, ObjectStore
from app.core.concurrency.offload import run_blocking


class AppFileManager:
    def __init__(
        self,
        app_id: UUID,
        *,
        root_path: str | Path | None = None,
        store: ObjectStore | None = None,
    ):
        """The store is shared across apps; the app id lives in the key.

        It used to live in the store's own prefix, which made the store
        per-tenant — a fresh one per app, and once stores were cached, a cache
        entry per app that nothing could ever evict. Prefixing here instead
        means one store per bucket, and the resulting object keys are
        byte-identical to what the prefixed store produced, so nothing needs
        migrating.
        """
        if (root_path is None) == (store is None):
            raise ValueError("Provide exactly one of root_path or store")

        self.app_id = app_id
        self.prefix = f"apps/{app_id}/"
        self._local_base: Path | None = (
            Path(root_path) / self.prefix if root_path else None
        )

        if root_path is not None:
            self.store = LocalStore(prefix=Path(root_path), mkdir=True)
        else:
            assert store is not None
            self.store = store

    def _key(self, path: str) -> str:
        """This app's object key for a caller-relative path."""
        return f"{self.prefix}{path.lstrip('/')}"

    def _local_path(self, path: str) -> Path:
        if self._local_base is None:
            raise RuntimeError("Local storage is not configured")
        return self._local_base / path

    async def read_file(self, path: str) -> bytes | str:
        try:
            result = await obs.get_async(self.store, self._key(path))
        except ObstoreNotFoundError:
            raise FileNotFoundError(f"File {path} not found")
        data = await result.bytes_async()
        bytes_data = data.to_bytes()
        try:
            return bytes_data.decode("utf-8")
        except UnicodeDecodeError:
            return bytes_data

    async def write_file(self, path: str, content: bytes | str | Path):
        if isinstance(content, str):
            content = content.encode("utf-8")

        await obs.put_async(
            self.store,
            self._key(path),
            content,
            use_multipart=isinstance(content, Path),
        )
        size = content.stat().st_size if isinstance(content, Path) else len(content)
        return {
            "name": path.split("/")[-1],
            "path": path,
            "size": size,
            "last_modified": datetime.now().isoformat(),
        }

    async def delete_file(self, path: str) -> None:
        try:
            await obs.delete_async(self.store, self._key(path))
        except ObstoreNotFoundError:
            return

    async def delete_prefix(self, prefix: str) -> None:
        normalized_prefix = prefix.rstrip("/")

        # Scoped to this app. Without the app's own prefix a bare `list()` on a
        # now-shared store would walk — and delete — every app in the bucket.
        list_prefix = (
            self._key(normalized_prefix)
            if normalized_prefix
            else self.prefix.rstrip("/")
        )
        # `async for`, not `for`. The stream supports both, and driving the
        # synchronous side from a coroutine means every page of the listing is a
        # blocking round trip to object storage on the event loop — once per
        # page, for as many pages as the release has files.
        async for chunk in self.store.list(prefix=list_prefix):
            paths = [item["path"] for item in chunk]
            if paths:
                await self.store.delete_async(paths)
        if self._local_base:
            target_dir = (
                self._local_base
                if not normalized_prefix
                else self._local_path(normalized_prefix)
            )
            # Recursive unlink over a whole release tree: filesystem work
            # proportional to the app, so it goes off the loop like the rest.
            await run_blocking(
                shutil.rmtree, target_dir, ignore_errors=True, limiter="cpu_bound"
            )
