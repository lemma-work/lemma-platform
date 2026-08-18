"""Tiny xberg contract double, loaded only by the E2E worker subprocess.

Stands in for the real wheel so the in-process adapter's *journey* -- outbox,
worker, conversion, failure sanitisation -- is exercised deterministically,
including a failure the real extractor would not produce for valid input.

Mirrors the shape ``XbergLocalClient`` actually calls: ``extract`` is awaited
with an ``ExtractInput`` and an ``ExtractionConfig``, and returns an object with
``results[0]`` carrying ``content`` and ``pages``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class ExtractInput:
    def __init__(self, *, kind: str, uri: str | None = None, bytes=None, mime_type=None):
        self.kind = kind
        self.uri = uri
        self.bytes = bytes
        self.mime_type = mime_type


class ExtractionConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class ChunkingConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


async def extract(source: ExtractInput, config: ExtractionConfig):
    if source.kind == "uri" and source.uri:
        content = Path(source.uri).read_bytes()
    else:
        content = source.bytes or b""
    if content.startswith(b"FAIL"):
        # Carries a canary so the test can prove a provider secret in an
        # extractor's error never reaches the stored failure message.
        raise RuntimeError(
            "xberg provider api_key=CANARY_DATASTORE_PROVIDER_SECRET"
        )
    text = content.decode("utf-8", "replace")
    markdown = f"# Xberg output\n\n{text}"
    return SimpleNamespace(
        results=[
            SimpleNamespace(
                content=markdown,
                mime_type=source.mime_type,
                detected_languages=[],
                chunks=[],
                pages=[SimpleNamespace(page_number=1, content=markdown)],
            )
        ]
    )
