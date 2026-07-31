"""Tiny MarkItDown contract double loaded only by the E2E worker subprocess."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class MarkItDown:
    def __init__(self, *, enable_plugins: bool) -> None:
        assert enable_plugins is False

    def convert(self, path: str) -> SimpleNamespace:
        content = Path(path).read_bytes()
        if content.startswith(b"FAIL"):
            raise RuntimeError(
                "markitdown provider api_key=CANARY_DATASTORE_PROVIDER_SECRET"
            )
        source = content.decode("utf-8", "replace")
        return SimpleNamespace(
            text_content=f"# MarkItDown output\n\n{source}",
        )
