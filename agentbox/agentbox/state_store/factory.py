from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlparse

from .postgres import PostgresStateStore
from .protocol import AsyncStateStore
from .sqlite import SQLiteStateStore


async def create_state_store(
    *,
    database_url: str | None,
    sqlite_path: str,
    durable_env_keys: Iterable[str] = ("LEMMA_BASE_URL",),
) -> AsyncStateStore:
    """Create the configured store without exposing credentials in errors."""

    keys = frozenset(key.strip() for key in durable_env_keys if key.strip())
    if not database_url:
        return await SQLiteStateStore.open(sqlite_path, durable_env_keys=keys)

    scheme = urlparse(database_url).scheme.lower()
    if scheme in {"postgres", "postgresql"}:
        return await PostgresStateStore.open(database_url, durable_env_keys=keys)
    if scheme == "sqlite":
        parsed = urlparse(database_url)
        if parsed.netloc not in {"", "localhost"}:
            raise ValueError("SQLite state URL must refer to a local path")
        path = unquote(parsed.path)
        if not path:
            raise ValueError("SQLite state URL must include a path")
        return await SQLiteStateStore.open(str(Path(path)), durable_env_keys=keys)
    raise ValueError(
        f"Unsupported AgentBox state database scheme: {scheme or '<none>'}"
    )
